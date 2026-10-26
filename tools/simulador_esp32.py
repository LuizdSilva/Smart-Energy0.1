"""Simula uma sala com ESP32, para testar o sistema sem o hardware.

Usa a mesma lógica do sketch (sensor de presença, tempo sem movimento, desligamento automático
e alerta) e as mesmas mensagens MQTT, então o sistema não percebe a diferença.

Como usar:
  python tools/simulador_esp32.py                     # sala A01 no broker.emqx.io
  python tools/simulador_esp32.py --host 127.0.0.1    # broker local
  python tools/simulador_esp32.py --predio L --sala L15 --limite 10

A presença alterna sozinha: a sala fica ocupada por um tempo e depois vazia (aí o alerta dispara).
A sala precisa estar cadastrada no sistema. Para parar, use Ctrl+C.
"""
import argparse
import json
import random
import time

import paho.mqtt.client as mqtt


class Sala:
    """Estado da sala e o que o loop() do sketch faz a cada ciclo."""

    def __init__(self, limite_s: float):
        self.limite_s = limite_s
        self.iluminacao = True
        self.ar_condicionado = True
        self.movimento = False
        self.alerta = False
        self.ja_houve_movimento = False
        self.contando = False
        self.inicio_ausencia = 0.0

    def atualizar(self, agora: float, movimento: bool) -> None:
        if movimento:
            if not self.movimento:  # voltou alguém: o alerta acaba
                self.alerta = False
            self.ja_houve_movimento = True
            self.contando = False
        elif self.ja_houve_movimento and not self.contando and not self.alerta and self.algo_ligado():
            self.contando, self.inicio_ausencia = True, agora
        self.movimento = movimento

        if self.contando and not movimento:
            if not self.algo_ligado():
                self.contando = False
            elif agora - self.inicio_ausencia >= self.limite_s:
                self.iluminacao = self.ar_condicionado = False
                self.alerta, self.contando = True, False

        # alerta ativo e alguém religou um equipamento: volta a contar
        if self.alerta and not movimento and self.algo_ligado() and not self.contando:
            self.alerta, self.contando, self.inicio_ausencia = False, True, agora

    def algo_ligado(self) -> bool:
        return self.iluminacao or self.ar_condicionado

    def aplicar_comando(self, equipamento: str, acao: str) -> bool:
        if acao not in ("ligar", "desligar"):
            return False
        ligar = acao == "ligar"
        if equipamento == "iluminacao":
            self.iluminacao = ligar
        elif equipamento == "ar_condicionado":
            self.ar_condicionado = ligar
        elif equipamento == "todos" and not ligar:
            self.iluminacao = self.ar_condicionado = False
        else:
            return False
        return True


def main() -> None:
    p = argparse.ArgumentParser(description="Simulador de sala com ESP32")
    p.add_argument("--host", default="broker.emqx.io")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--base", default="smartenergy/facens/demo-48271", help="tópico base (igual ao do sistema)")
    p.add_argument("--predio", default="A")
    p.add_argument("--sala", default="A01")
    p.add_argument("--limite", type=float, default=20, help="segundos sem movimento até o alerta")
    args = p.parse_args()

    topico_estado = f"{args.base}/sala-{args.sala.lower()}/estado"
    topico_comando = f"{args.base}/sala-{args.sala.lower()}/comando"
    sala = Sala(args.limite)
    publicar_ja = [False]  # depois de um comando, publica o novo estado sem esperar os 3 s

    def ao_conectar(client, userdata, flags, reason_code, properties=None):
        print("conectado ao broker:", reason_code)
        client.subscribe(topico_comando)
        publicar_ja[0] = True

    def ao_receber(client, userdata, msg):
        try:
            comando = json.loads(msg.payload)
            aplicado = sala.aplicar_comando(comando.get("equipamento"), comando.get("acao"))
        except (ValueError, AttributeError):
            aplicado = False
        print("comando recebido:", msg.payload.decode(errors="replace"), "->", "aplicado" if aplicado else "ignorado")
        publicar_ja[0] = True

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{args.sala}-{random.randint(0, 99999):05d}")
    cliente.on_connect, cliente.on_message = ao_conectar, ao_receber
    cliente.connect(args.host, args.port, keepalive=30)
    cliente.loop_start()

    print(f"Simulando {args.predio}/{args.sala} -> {topico_estado} (alerta após {args.limite:g}s sem movimento)")
    presente, ultimo_estado, ultimo_envio = False, "", 0.0
    proxima_troca = time.time() + 5
    try:
        while True:
            agora = time.time()
            if agora >= proxima_troca:
                presente = not presente
                # ocupada por 15-30 s; vazia por tempo suficiente para o alerta disparar
                proxima_troca = agora + (random.uniform(15, 30) if presente else args.limite + random.uniform(15, 40))
            sala.atualizar(agora, presente)

            estado = json.dumps({"predio": args.predio, "sala": args.sala, "movimento": sala.movimento,
                                 "iluminacao": sala.iluminacao, "ar_condicionado": sala.ar_condicionado,
                                 "alerta": sala.alerta})
            if estado != ultimo_estado or agora - ultimo_envio >= 3 or publicar_ja[0]:
                cliente.publish(topico_estado, estado)
                ultimo_estado, ultimo_envio, publicar_ja[0] = estado, agora, False
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        cliente.loop_stop()
        cliente.disconnect()


if __name__ == "__main__":
    main()

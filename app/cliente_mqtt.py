"""Cliente MQTT (paho-mqtt 2.x): assina os estados e publica comandos.

Roda numa thread própria (loop_start) com reconexão automática. Cada mensagem abre uma
conexão SQLite curta, então não há compartilhamento de conexão entre threads.
"""
import json
import logging
import random
import threading

import paho.mqtt.client as mqtt

from . import banco_dados, regras_tempo_real

log = logging.getLogger("smartenergy.mqtt")


class MqttService:
    def __init__(self, cfg: dict, client_factory=None):
        self.cfg = cfg
        self.base = cfg["MQTT_BASE_TOPIC"]
        self.connected = False
        self.last_message_at: str | None = None
        self.last_error: str | None = None
        self.messages_ok = 0
        self.messages_rejected = 0
        self._lock = threading.Lock()
        self._client_factory = client_factory
        self.client = None

    # ---- tópicos -------------------------------------------------------
    @property
    def state_filter(self) -> str:
        return f"{self.base}/+/estado"

    def command_topic(self, sala: str) -> str:
        return f"{self.base}/{regras_tempo_real.sala_slug(sala)}/comando"

    # ---- ciclo de vida -------------------------------------------------
    def start(self) -> None:
        if self._client_factory:
            self.client = self._client_factory()
        else:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                      client_id=f"smartenergy-backend-{random.randint(0, 99999):05d}")
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        try:
            self.client.connect_async(self.cfg["MQTT_HOST"], self.cfg["MQTT_PORT"], keepalive=30)
            self.client.loop_start()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            log.error("MQTT start falhou: %s", exc)

    def stop(self) -> None:
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

    # ---- callbacks -----------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        ok = not getattr(reason_code, "is_failure", reason_code != 0)
        if ok:
            self.connected, self.last_error = True, None
            client.subscribe(self.state_filter, qos=1)   # reassina a cada (re)conexão
            log.info("MQTT conectado; assinando %s", self.state_filter)
        else:
            self.connected, self.last_error = False, f"conexão recusada: {reason_code}"
            log.error(self.last_error)

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        log.warning("MQTT desconectado")

    def _on_message(self, client, userdata, msg):
        self.handle_message(msg.topic, msg.payload)

    def handle_message(self, topic: str, raw: bytes) -> bool:
        """Processa um estado. Público para facilitar testes sem broker."""
        parts = topic.split("/")
        expected = self.base.split("/")
        if len(parts) != len(expected) + 2 or parts[:-2] != expected or parts[-1] != "estado":
            return self._reject(f"tópico inesperado: {topic}")
        if len(raw) > 2048:
            return self._reject("payload grande demais")
        try:
            st = regras_tempo_real.validate_state(json.loads(raw.decode("utf-8")), topic_slug=parts[-2])
        except (ValueError, UnicodeDecodeError) as exc:   # json.JSONDecodeError e InvalidState são ValueError
            return self._reject(f"{topic}: {exc}")
        conn = banco_dados.connect(self.cfg["DB_PATH"])
        try:
            regras_tempo_real.process_state(conn, st, store_interval_s=self.cfg["STORE_INTERVAL_S"],
                               command_timeout_s=self.cfg["COMMAND_TIMEOUT_S"])
        except regras_tempo_real.SalaNaoCadastrada as exc:
            return self._reject(f"{exc} (cadastre em Tempo Real > Cadastrar salas)")
        except Exception:  # noqa: BLE001  (nunca derrubar a thread MQTT)
            log.exception("erro ao processar estado")
            return self._reject("erro interno")
        finally:
            conn.close()
        with self._lock:
            self.messages_ok += 1
            self.last_message_at = regras_tempo_real.iso(regras_tempo_real.utcnow())
        return True

    def _reject(self, why: str) -> bool:
        with self._lock:
            self.messages_rejected += 1
        log.warning("mensagem MQTT descartada: %s", why)
        return False

    # ---- comandos ------------------------------------------------------
    def publish_command(self, sala: str, equipamento: str, acao: str) -> bool:
        if not self.client or not self.connected:
            return False
        payload = json.dumps({"equipamento": equipamento, "acao": acao})
        info = self.client.publish(self.command_topic(sala), payload, qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def status(self) -> dict:
        return {"conectado": self.connected, "broker": f"{self.cfg['MQTT_HOST']}:{self.cfg['MQTT_PORT']}",
                "topico_estado": self.state_filter, "ultima_mensagem": self.last_message_at,
                "mensagens_ok": self.messages_ok, "mensagens_descartadas": self.messages_rejected,
                "erro": self.last_error}

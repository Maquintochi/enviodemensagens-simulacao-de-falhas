#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import sys
import json
import queue
import socket
import threading
import time
import uuid
import random
from dataclasses import dataclass
from typing import Dict, Optional
import tkinter as tk
from tkinter import ttk, scrolledtext

HOST = "127.0.0.1"

def json_line(sock):
    data = b""
    while True:
        ch = sock.recv(1)
        if not ch:
            break
        data += ch
        if ch == b'\n':
            break
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8").strip())
    except Exception:
        return None

def send_json_line(sock, obj):
    line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    sock.sendall(line)

@dataclass
class MessageStatus:
    id: str
    text: str
    ts: float
    attempts: int = 0
    ack1: bool = False
    ack2: bool = False
    last_error: Optional[str] = None
    next_retry_ms: Optional[int] = None
    state: str = "new"  # new | queued | sending | ack1 | delivered | failed

class ServerThread(threading.Thread):
    def __init__(self, name, port, inbox_queue, ui_flags):
        super().__init__(daemon=True)
        self.name = name
        self.port = port
        self.inbox_queue = inbox_queue
        self.ui_flags = ui_flags  # Tk vars
        self.socket = None
        self.stop_event = threading.Event()

    def _safe_flag(self, key, default=False):
        try:
            return bool(self.ui_flags[key].get())
        except Exception:
            return default

    def run(self):
        while not self.stop_event.is_set():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((HOST, self.port))
                    s.listen(5)
                    self.socket = s
                    while not self.stop_event.is_set():
                        try:
                            s.settimeout(0.5)
                            conn, addr = s.accept()
                        except socket.timeout:
                            continue
                        threading.Thread(target=self.handle_conn, args=(conn, addr), daemon=True).start()
            except OSError:
                time.sleep(1.0)

    def handle_conn(self, conn, addr):
        with conn:
            # Não rejeita a conexão antes de ler o tipo;
            # assim podemos responder PING mesmo em alguns modos.
            conn.settimeout(2.0)
            msg = json_line(conn)
            if not msg:
                return

            mtype = msg.get("type")

            # PING → PONG (somente se não estiver em rejeição dura)
            if mtype == "PING":
                if not self._safe_flag("reject_conn", False):
                    try:
                        send_json_line(conn, {"type": "PONG"})
                    except Exception:
                        pass
                return

            # Para outras mensagens, respeita flags de simulação
            if self._safe_flag("reject_conn", False):
                return

            try:
                proc_delay = int(self.ui_flags["proc_delay_ms"].get())
            except Exception:
                proc_delay = 0
            if proc_delay > 0:
                time.sleep(proc_delay / 1000.0)

            if mtype == "MSG":
                mid = msg.get("id")
                text = msg.get("text", "")
                sender = msg.get("sender", "?")
                try:
                    reply_to = int(msg.get("reply_to_port", 0)) or None
                except Exception:
                    reply_to = None

                self.inbox_queue.put(("RECV", {"id": mid, "from": sender, "text": text}))

                if self._safe_flag("crash_before_ack", False):
                    return

                try:
                    send_json_line(conn, {"type": "ACK", "id": mid})
                except Exception:
                    return

                if reply_to:
                    time.sleep(0.1)
                    try:
                        with socket.create_connection((HOST, reply_to), timeout=2.0) as c2:
                            send_json_line(c2, {"type": "DELIVERED", "id": mid})
                    except Exception:
                        pass

            elif mtype == "DELIVERED":
                mid = msg.get("id")
                self.inbox_queue.put(("ACK2", {"id": mid}))
            else:
                self.inbox_queue.put(("INFO", f"Recebido {mtype}: {msg}"))

    def stop(self):
        self.stop_event.set()
        try:
            if self.socket:
                self.socket.close()
        except Exception:
            pass

class NodeApp:
    def __init__(self, name, port, peer_port, simple_mode=False):
        self.name = name
        self.port = port
        self.peer_port = peer_port
        self.simple_mode = simple_mode

        self.root = tk.Tk()
        title_suffix = "— Modo Apresentação" if self.simple_mode else ""
        self.root.title(f"Chat {self.name} — porta {self.port} -> peer {self.peer_port} {title_suffix}")
        self.root.geometry("760x640" if self.simple_mode else "780x700")

        # Flags
        self.out_delay_ms = tk.IntVar(value=0)
        self.out_drop_pct = tk.IntVar(value=0)
        self.out_duplicate = tk.BooleanVar(value=False)
        self.drop_next = tk.BooleanVar(value=False)
        self.crash_before_ack = tk.BooleanVar(value=False)
        self.proc_delay_ms = tk.IntVar(value=0)
        self.reject_conn = tk.BooleanVar(value=False)
        self.ack_timeout_ms = tk.IntVar(value=1500 if self.simple_mode else 3000)
        self.max_retries = tk.IntVar(value=4 if self.simple_mode else 3)

        # Tratamento de falhas / Outbox / Dedup
        self.fail_treatment = tk.BooleanVar(value=False)
        self.dedup_by_id = tk.BooleanVar(value=True)

        self.ui_flags = {
            "out_delay_ms": self.out_delay_ms,
            "out_drop_pct": self.out_drop_pct,
            "out_duplicate": self.out_duplicate,
            "drop_next": self.drop_next,
            "crash_before_ack": self.crash_before_ack,
            "proc_delay_ms": self.proc_delay_ms,
            "reject_conn": self.reject_conn,
            "ack_timeout_ms": self.ack_timeout_ms,
            "max_retries": self.max_retries,
        }

        self.inbox_queue = queue.Queue()
        self.server = ServerThread(self.name, self.port, self.inbox_queue, self.ui_flags)
        self.server.start()

        self.pending: Dict[str, MessageStatus] = {}
        self.outbox: Dict[str, MessageStatus] = {}
        self.seen_ids = {}
        self.seen_ids_max = 1000  # LRU simples

        self._watcher_active = False

        self._build_ui()
        self.root.after(100, self._poll_inbox)

    # ---------------- UI ----------------
    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=8)
        frm.pack(fill="both", expand=True)

        top = ttk.Frame(frm)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text=f"Eu: {self.name} (porta {self.port}) → Par: {self.peer_port}").pack(side="left")

        opts = ttk.Frame(frm)
        opts.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(opts, text="Tratamento de falhas (Outbox + auto-flush)", variable=self.fail_treatment).pack(side="left")
        ttk.Checkbutton(opts, text="De-duplicar por ID (receptor)", variable=self.dedup_by_id).pack(side="left", padx=(12,0))

        mid = ttk.Frame(frm)
        mid.pack(fill="both", expand=True)

        self.txt = scrolledtext.ScrolledText(mid, height=14, wrap="word", font=("Consolas", 10))
        self.txt.pack(side="top", fill="both", expand=True)
        self.txt.insert("end", "🟢 Aplicativo iniciado. Abra outra instância com portas invertidas.\n")

        sendbar = ttk.Frame(mid)
        sendbar.pack(fill="x", pady=(6, 6))
        self.entry = ttk.Entry(sendbar)
        self.entry.pack(side="left", fill="x", expand=True)
        ttk.Button(sendbar, text="Enviar", command=self.on_send).pack(side="left", padx=(6, 0))
        self.entry.bind("<Return>", lambda e: self.on_send())

        lab = ttk.LabelFrame(frm, text="Cenário de falha (Modo Apresentação)" if self.simple_mode else "Simulações de Falha")
        lab.pack(fill="x")

        row = ttk.Frame(lab)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="Cenário:").pack(side="left")
        self.scenario = tk.StringVar(value="Nenhum")
        combo = ttk.Combobox(row, textvariable=self.scenario, state="readonly",
                             values=["Nenhum", "Queda aleatória (50%)", "Travar antes do ACK₁ (destino)",
                                     "Atraso no destino (1500ms)", "Partição (rejeitar conexões)",
                                     "Duplicar envio"])
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", self.apply_scenario)
        ttk.Button(row, text="Reset", command=self.reset_scenario).pack(side="left", padx=(8,0))

        # ---- OUTBOX (visual) ----
        out_frame = ttk.LabelFrame(frm, text="Outbox (mensagens pendentes / em trânsito)")
        out_frame.pack(fill="both", expand=False, pady=(8,0))

        header = ttk.Frame(out_frame)
        header.pack(fill="x")
        self.outbox_count_str = tk.StringVar(value="Outbox: 0")
        ttk.Label(header, textvariable=self.outbox_count_str).pack(side="left")

        cols = ("id", "texto", "estado", "tentativas", "erro")
        self.outbox_tv = ttk.Treeview(out_frame, columns=cols, show="headings", height=8)
        for col, w in zip(cols, (120, 280, 90, 80, 220)):
            self.outbox_tv.heading(col, text=col.capitalize())
            self.outbox_tv.column(col, width=w, stretch=(col=="texto" or col=="erro"))
        self.outbox_tv.pack(fill="both", expand=True)

        foot = ttk.Frame(frm)
        foot.pack(fill="x", pady=(6, 0))
        ttk.Button(foot, text="Sair", command=self.on_close).pack(side="right")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------- Outbox helpers -------------
    def _outbox_add(self, st: MessageStatus):
        st.state = "queued"
        self.outbox[st.id] = st
        self._outbox_sync_row(st)
        self._update_outbox_counter()
        self.log(f"📦 Guardada na Outbox: {st.id[:8]}")
        self._ensure_watcher_running()

    def _outbox_sync_row(self, st: MessageStatus):
        iid = st.id
        values = (iid[:8], st.text[:64], st.state, st.attempts, (st.last_error or "")[:64])
        if self.outbox_tv.exists(iid):
            self.outbox_tv.item(iid, values=values)
        else:
            self.outbox_tv.insert("", "end", iid=iid, values=values)

    def _outbox_update_state(self, mid: str, state: str):
        st = self.outbox.get(mid)
        if not st:
            return
        st.state = state
        self._outbox_sync_row(st)

    def _outbox_remove(self, mid: str):
        if mid in self.outbox:
            del self.outbox[mid]
        if self.outbox_tv.exists(mid):
            self.outbox_tv.delete(mid)
        self._update_outbox_counter()

    def _update_outbox_counter(self):
        self.outbox_count_str.set(f"Outbox: {len(self.outbox)}")

    # ------------- Connectivity Watcher -------------
    def _ensure_watcher_running(self):
        if not self._watcher_active:
            self._watcher_active = True
            self.root.after(500, self._connectivity_tick)

    def _peer_is_ready(self) -> bool:
        """Conecta, envia PING e aguarda PONG para considerar o peer pronto."""
        try:
            with socket.create_connection((HOST, self.peer_port), timeout=0.8) as s:
                s.settimeout(0.6)
                send_json_line(s, {"type": "PING"})
                resp = json_line(s)
                return bool(resp and resp.get("type") == "PONG")
        except Exception:
            return False

    def _connectivity_tick(self):
        if not self.outbox:
            self._watcher_active = False
            return

        if self._peer_is_ready():
            for mid, st in list(self.outbox.items()):
                if st.state in ("queued", "failed") and not st.ack1:
                    self.log(f"🚚 Outbox: reenviando {mid[:8]}")
                    self._outbox_update_state(mid, "sending")
                    self._attempt_send(mid, st, attempt=st.attempts + 1)

        self.root.after(500, self._connectivity_tick)

    # ---------------- Core ----------------
    def log(self, line):
        self.txt.insert("end", line + "\n")
        self.txt.see("end")

    def apply_scenario(self, *_):
        self.reset_scenario()
        sc = self.scenario.get()
        if sc == "Queda aleatória (50%)":
            self.out_drop_pct.set(50)
        elif sc == "Travar antes do ACK₁ (destino)":
            self.crash_before_ack.set(True)
        elif sc == "Atraso no destino (1500ms)":
            self.proc_delay_ms.set(1500)
        elif sc == "Partição (rejeitar conexões)":
            self.reject_conn.set(True)
        elif sc == "Duplicar envio":
            self.out_duplicate.set(True)

    def reset_scenario(self):
        self.out_delay_ms.set(0)
        self.out_drop_pct.set(0)
        self.out_duplicate.set(False)
        self.drop_next.set(False)
        self.crash_before_ack.set(False)
        self.proc_delay_ms.set(0)
        self.reject_conn.set(False)

    def on_send(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.send_message(text)

    def send_message(self, text):
        mid = str(uuid.uuid4())
        st = MessageStatus(id=mid, text=text, ts=time.time())
        self.pending[mid] = st
        self.log(f"📤 [{self.name}] enviando {mid[:8]}: {text}")

        # Pré-registro na Outbox somente quando o usuário ativou o tratamento de falhas
        if self.fail_treatment.get() and mid not in self.outbox:
            self._outbox_add(st)
            self._outbox_update_state(mid, "sending")

        self._attempt_send(mid, st, attempt=1)

    def _attempt_send(self, mid, st: MessageStatus, attempt: int):
        st.attempts = attempt

        if self.drop_next.get():
            self.drop_next.set(False)
            self.log(f"⚠️  (Simulado) PRÓXIMO envio derrubado: {mid[:8]}")
            if mid not in self.outbox:
                self._outbox_add(st)
            self._schedule_retry(mid, st)
            return

        drop_pct = max(0, min(100, int(self.out_drop_pct.get())))
        if drop_pct > 0 and random.randint(1, 100) <= drop_pct:
            self.log(f"⚠️  (Simulado) Queda aleatória {drop_pct}%: {mid[:8]}")
            if mid not in self.outbox:
                self._outbox_add(st)
            self._schedule_retry(mid, st)
            return

        out_delay = max(0, int(self.out_delay_ms.get()))
        if out_delay > 0:
            self.log(f"⏳ Atraso de envio {out_delay}ms para {mid[:8]}")
            self.root.after(out_delay, lambda: self._do_send(mid, st))
        else:
            self._do_send(mid, st)

    def _do_send(self, mid, st: MessageStatus):
        payload = {
            "type": "MSG",
            "id": mid,
            "text": st.text,
            "sender": self.name,
            "reply_to_port": self.port,
            "ts": time.time(),
        }

        def one_send():
            try:
                with socket.create_connection((HOST, self.peer_port), timeout=2.5) as s:
                    s.settimeout(max(0.2, int(self.ack_timeout_ms.get()) / 1000.0 + 0.3))
                    send_json_line(s, payload)
                    resp = json_line(s)
                    if not resp or resp.get("type") != "ACK" or resp.get("id") != mid:
                        raise TimeoutError("ACK₁ não recebido")
                    st.ack1 = True
                    self.log(f"✅ ACK₁ recebido para {mid[:8]}")
                return True
            except Exception as e:
                st.last_error = str(e)
                return False

        ok = one_send()

        if self.out_duplicate.get():
            threading.Thread(target=lambda: self._duplicate_fire_and_forget(payload), daemon=True).start()

        if ok:
            if mid in self.outbox:
                self._outbox_update_state(mid, "ack1")
        else:
            self.log(f"❌ Falha ao enviar {mid[:8]}: {st.last_error}")
            # Garantir Outbox SEMPRE na falha
            if mid not in self.outbox:
                self._outbox_add(st)
            else:
                self._outbox_update_state(mid, "queued")
                self._outbox_sync_row(st)
            self._schedule_retry(mid, st)

    def _duplicate_fire_and_forget(self, payload):
        time.sleep(0.05)
        try:
            with socket.create_connection((HOST, self.peer_port), timeout=1.5) as s:
                s.settimeout(1.0)
                send_json_line(s, payload)
        except Exception:
            pass

    def _schedule_retry(self, mid, st: MessageStatus):
        attempts = st.attempts
        maxr = max(0, int(self.max_retries.get()))
        if attempts >= maxr:
            self.log(f"🛑 Sem mais tentativas para {mid[:8]} (tentativas={attempts})")
            # mantém/insere na Outbox como 'failed' para auto-flush posterior
            if mid not in self.outbox:
                self._outbox_add(st)
            self._outbox_update_state(mid, "failed")
            return
        delay_ms = 1000 * (2 ** (attempts - 1))
        st.next_retry_ms = delay_ms
        self.log(f"🔁 Retentativa #{attempts+1} para {mid[:8]} em {delay_ms}ms")
        self.root.after(delay_ms, lambda: self._attempt_send(mid, st, attempt=attempts+1))

    # ---------------- Inbox / events ----------------
    def _poll_inbox(self):
        try:
            while True:
                ev, data = self.inbox_queue.get_nowait()
                if ev == "RECV":
                    mid = data["id"]
                    sender = data["from"]
                    text = data["text"]
                    if self.dedup_by_id.get():
                        if mid in self.seen_ids:
                            self.log(f"🧹 Duplicata descartada (id={mid[:8]})")
                            self.seen_ids[mid] = time.time()
                            if len(self.seen_ids) > self.seen_ids_max:
                                self._prune_seen()
                            continue
                        else:
                            self.seen_ids[mid] = time.time()
                            if len(self.seen_ids) > self.seen_ids_max:
                                self._prune_seen()

                    self.log(f"📥 [{sender}] → [{self.name}] recebeu {mid[:8]}: {text}")

                elif ev == "INFO":
                    self.log(f"ℹ️  {data}")

                elif ev == "ACK2":
                    mid = data["id"]
                    st = self.pending.get(mid)
                    if st:
                        st.ack2 = True
                    self.log(f"📬 ACK₂ (Entregue) para {mid[:8]}")
                    if mid in self.outbox:
                        self._outbox_remove(mid)

        except queue.Empty:
            pass

        self.root.after(100, self._poll_inbox)

    def _prune_seen(self):
        oldest_key = min(self.seen_ids, key=self.seen_ids.get)
        if oldest_key:
            del self.seen_ids[oldest_key]

    def on_close(self):
        try:
            self.server.stop()
        except Exception:
            pass
        self.root.destroy()

def main():
    def get_arg(flag, cast=str, default=None):
        if flag in sys.argv:
            try:
                return cast(sys.argv[sys.argv.index(flag) + 1])
            except Exception:
                return default
        return default

    name = get_arg("--name", str, "B")
    port = get_arg("--port", int, 5000)
    peer = get_arg("--peer", int, 5001)
    simple = ("--simple" in sys.argv) or True

    app = NodeApp(name, port, peer, simple_mode=simple)
    app.root.mainloop()

if __name__ == "__main__":
    main()

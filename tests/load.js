import http from "k6/http";
import ws from "k6/ws";
import { check, sleep } from "k6";

export let options = {
  vus: __ENV.VUS ? parseInt(__ENV.VUS) : 50,
  duration: __ENV.DURATION || "1m",
  thresholds: {
    http_req_duration: ["p(95)<2000"],
  },
};

const BASE = __ENV.BASE || "http://127.0.0.1:8000";

function submitJob(q1 = 1, q2 = 2, username = "load") {
  const payload = JSON.stringify({ q1, q2, username });
  const res = http.post(`${BASE}/submit`, payload, { headers: { "Content-Type": "application/json" } });
  check(res, { "submit 2xx": (r) => r.status === 200 || r.status === 201 });
  return res.json().task_id;
}

export default function () {
  const taskId = submitJob();

  const url = `${BASE.replace("http", "ws")}/ws/${taskId}`;
  let done = false;

  const res = ws.connect(url, null, function (socket) {
    socket.on("open", function () {
    });
    socket.on("message", function (msg) {
      try {
        const m = JSON.parse(msg);
        if (m.status === "done") {
          done = true;
          socket.close();
        }
      } catch (e) {}
    });
    socket.on("close", function () {});
    socket.on("error", function () {});
    socket.setInterval(function () {
      if (!done) socket.send("ping");
    }, 5000);
  });

  check(res, { "ws connect ok": (r) => r && r.status === 101 });

  sleep(0.01);
}
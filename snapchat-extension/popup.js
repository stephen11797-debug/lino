const SERVER = "http://localhost:8001";
const userEl = document.getElementById("user");
const friendEl = document.getElementById("friend");
const msgEl = document.getElementById("msg");
const timeEl = document.getElementById("time");
const statusEl = document.getElementById("status");
const pendingEl = document.getElementById("pending");
const pending = [];

timeEl.value = new Date().toISOString().slice(0, 16);

let loadTimeout;
userEl.addEventListener("input", () => {
  clearTimeout(loadTimeout);
  loadTimeout = setTimeout(loadFriends, 500);
});

async function loadFriends() {
  const user = userEl.value.trim();
  friendEl.innerHTML = "<option value=''>Loading...</option>";
  if (!user) { friendEl.innerHTML = "<option value=''>Type username first...</option>"; return; }
  try {
    const r = await fetch(SERVER + "/api/friends", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user}),
    });
    const d = await r.json();
    friendEl.innerHTML = "";
    (d.friends || []).forEach(f => {
      const opt = document.createElement("option");
      opt.value = f; opt.textContent = f;
      friendEl.appendChild(opt);
    });
    if (!d.friends || !d.friends.length) friendEl.innerHTML = "<option value=''>No friends found</option>";
  } catch(e) {
    friendEl.innerHTML = "<option value=''>Server not running</option>";
  }
}

document.getElementById("schedule").addEventListener("click", async () => {
  const user = userEl.value.trim();
  const friend = friendEl.value;
  const text = msgEl.value.trim();
  const timeStr = timeEl.value;
  if (!user || !friend || !text || !timeStr) { statusEl.textContent = "Fill in all fields"; return; }

  const sendAt = new Date(timeStr).getTime();
  if (isNaN(sendAt) || sendAt <= Date.now()) { statusEl.textContent = "Pick a future time"; return; }

  const delay = sendAt - Date.now();
  const id = Date.now().toString();
  const entry = {id, user, friend, text, sendAt};
  pending.push(entry);

  statusEl.textContent = "Scheduled for " + new Date(sendAt).toLocaleTimeString();
  msgEl.value = "";
  renderPending();

  setTimeout(async () => {
    try {
      const r = await fetch(SERVER + "/api/chat/send", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({from: user, to: friend, text, ttl: 0}),
      });
      const d = await r.json();
      if (d.ok) statusEl.textContent = "Sent to @" + friend + "!";
      else statusEl.textContent = "Failed: " + (d.error || "unknown");
    } catch(e) {
      statusEl.textContent = "Failed: server not reachable";
    }
    const idx = pending.findIndex(p => p.id === id);
    if (idx >= 0) pending.splice(idx, 1);
    renderPending();
  }, delay);
});

function renderPending() {
  pendingEl.innerHTML = "";
  pending.forEach(p => {
    const remaining = Math.max(0, p.sendAt - Date.now());
    const when = remaining > 60000 ? "in " + Math.ceil(remaining/60000) + "m" : "in " + Math.ceil(remaining/1000) + "s";
    const div = document.createElement("div");
    div.className = "pending-item";
    div.innerHTML = "<span>-> @" + p.friend + ": '" + p.text.slice(0,25) + "' (" + when + ")</span>";
    const btn = document.createElement("button");
    btn.textContent = "Cancel";
    btn.onclick = () => { const i = pending.indexOf(p); if(i>=0) pending.splice(i,1); renderPending(); };
    div.appendChild(btn);
    pendingEl.appendChild(div);
  });
}

setInterval(renderPending, 5000);

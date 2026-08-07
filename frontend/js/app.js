/* JL-Agent 前端入口（P1 骨架）：健康检查 */
(async () => {
  const el = document.getElementById("health-status");
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    if (j.code === 0) {
      const rules = Object.keys(j.data.rules).join(" / ");
      el.textContent = "服务正常 · 规则已加载: " + rules;
      el.className = "ok";
    } else {
      el.textContent = "异常: " + j.message;
      el.className = "bad";
    }
  } catch (e) {
    el.textContent = "无法连接后端: " + e.message;
    el.className = "bad";
  }
})();

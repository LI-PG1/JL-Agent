/* app.js：JL-Agent 工作台控制器 —— 简历列表 / 表单 / 保存 / 生成（SSE）/ 预览联动 */
(function () {
  "use strict";

  var state = window.JL = window.JL || {};
  var es = null;

  function $id(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function monthToInput(v) { return (v || "").replace(".", "-"); }
  function inputToMonth(v) { return (v || "").replace("-", "."); }
  function list(v) { return String(v || "").split(/[,，、]/).map(function (s) { return s.trim(); }).filter(Boolean); }
  // r3：自然描述 → 按「空行」分段（连续换行不拆分），每段作为一条待 AI 润色文本
  function paragraphs(v) {
    return String(v || "").split(/\n\s*\n/).map(function (s) { return s.trim(); }).filter(Boolean);
  }
  function errMsg(j) {
    if (j && j.message) return j.message;
    if (j && Array.isArray(j.detail)) {
      return j.detail.map(function (d) { return d.msg || JSON.stringify(d); }).join("；");
    }
    if (j && j.detail) return String(j.detail);
    return "未知错误";
  }

  /* ---------------- 健康检查 ---------------- */
  function health() {
    fetch("/api/health").then(function (r) { return r.json(); }).then(function (j) {
      var el = $id("health-status");
      if (j.code === 0) {
        el.textContent = "服务正常";
        el.className = "ok";
      } else {
        el.textContent = "异常: " + j.message;
        el.className = "bad";
      }
    }).catch(function () {
      var el = $id("health-status");
      el.textContent = "无法连接后端";
      el.className = "bad";
    });
  }

  /* ---------------- 简历列表 ---------------- */
  function loadList() {
    return fetch("/api/resume").then(function (r) { return r.json(); }).then(function (j) {
      var ul = $id("resume-list");
      ul.innerHTML = "";
      (j.data.items || []).forEach(function (it) {
        var li = document.createElement("li");
        if (state.resumeId === it.id) li.className = "active";
        var main = document.createElement("div");
        main.className = "li-main";
        main.textContent = (it.name || "未命名") + (it.direction ? " · " + it.direction : "");
        var sub = document.createElement("div");
        sub.className = "li-sub";
        sub.textContent = "更新于 " + (it.updated_at || "").slice(0, 16).replace("T", " ") +
          " · " + (it.file || "");
        var del = document.createElement("span");
        del.className = "del";
        del.textContent = "删除";
        del.addEventListener("click", function (ev) {
          ev.stopPropagation();
          // 乐观删除（§r3）：立即移除条目，后台请求；失败回滚为真实列表
          ul.removeChild(li);
          fetch("/api/resume/" + it.id, { method: "DELETE" })
            .then(function (r) { return r.json(); })
            .then(function (jr) {
              if (jr.code !== 0) { loadList(); return; }
              if (state.resumeId === it.id) newResume();
            })
            .catch(function () { loadList(); });
        });
        li.appendChild(main);
        li.appendChild(sub);
        li.appendChild(del);
        li.addEventListener("click", function () { openResume(it.id); });
        ul.appendChild(li);
      });
      return j;
    }).catch(function (e) { console.warn("加载列表失败", e); });
  }

  function newResume() {
    state.resumeId = null;
    state.resume = null;
    clearForm();
    $id("cur-resume").textContent = "未保存（新建）";
    $id("btn-generate").disabled = true;
    loadList();
  }

  function openResume(id) {
    fetch("/api/resume/" + id).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      state.resumeId = id;
      state.resume = j.data;
      state.config = null;
      state.html = null;
      fillForm(j.data);
      $id("cur-resume").textContent = ((j.data.basicInfo || {}).name || "未命名");
      $id("btn-generate").disabled = false;
      loadList();
    }).catch(function (e) {
      Adapt.showBanner("打开简历失败：" + e.message, true);
    });
  }

  /* ---------------- 表单行模板 ---------------- */
  // 枚举值与后端一致（§3.4）：degree/category 使用中文 value
  var DEGREES = [["专科", "专科"], ["学士", "学士"], ["硕士", "硕士"], ["博士", "博士"]];
  var CATS = [["专业技能", "专业技能"], ["工具与框架", "工具与框架"], ["语言能力", "语言能力"],
              ["算法与模型", "算法与模型"], ["数据与统计", "数据与统计"], ["工程实践", "工程实践"],
              ["证书资质", "证书资质"], ["兴趣爱好", "兴趣爱好"], ["其他能力", "其他能力"]];
  // r2/r3：职责/要点整行 + 高度 ×3；允许自然描述（每行一条 → 自然语言，AI 后续润色加工）
  var ROW_TMPL = {
    edu: '<div class="grid">' +
      '<label>学校<input class="in-school" maxlength="64"></label>' +
      '<label>专业<input class="in-major" maxlength="64"></label>' +
      '<label>学历<select class="in-degree"></select></label>' +
      '<label>开始<input class="in-start" type="month" required></label>' +
      '<label>结束<input class="in-end" type="month" required></label>' +
      '</div>',
    int: '<div class="grid">' +
      '<label>公司<input class="in-company" maxlength="64"></label>' +
      '<label>职位<input class="in-position" maxlength="64"></label>' +
      '<label>开始<input class="in-start" type="month" required></label>' +
      '<label>结束<input class="in-end" type="month" required></label>' +
      '<label class="full">职责（自然描述，AI 将自动润色整理）<textarea class="in-duties" rows="9" maxlength="4000"></textarea></label>' +
      '</div>',
    proj: '<div class="grid">' +
      '<label>项目名称<input class="in-name" maxlength="64"></label>' +
      '<label>角色<input class="in-role" maxlength="32"></label>' +
      '<label>开始<input class="in-start" type="month"></label>' +
      '<label>结束<input class="in-end" type="month"></label>' +
      '<label class="full">技术栈（逗号分隔）<input class="in-stack" maxlength="300"></label>' +
      '<label class="full">要点（自然描述，AI 将自动润色整理）<textarea class="in-items" rows="12" maxlength="6000"></textarea></label>' +
      '</div>',
    skill: '<div class="grid">' +
      '<label>分类<select class="in-category"></select></label>' +
      '<label>技能<input class="in-name" maxlength="64"></label>' +
      '</div>',
    honor: '<div class="grid">' +
      '<label>奖项<input class="in-name" maxlength="128"></label>' +
      '<label>机构<input class="in-org" maxlength="64"></label>' +
      '<label>时间<input class="in-time" maxlength="32"></label>' +
      '</div>',
    job: '<div class="grid">' +
      '<label class="full">岗位名称<input class="in-title" maxlength="64"></label>' +
      '<label class="full">JD 原文<textarea class="in-jd" rows="5" maxlength="20000"></textarea></label>' +
      '</div>',
  };
  // r1：条目自动编号（按添加时间先后 = DOM 顺序）；实习/项目独立前缀
  var IDX_NUMS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];

  function reindex(sec) {
    var prefix = sec === "int" ? "实习" : (sec === "proj" ? "项目" : "");
    if (!prefix) return;
    var rows = $id(sec + "-rows").querySelectorAll(".row");
    rows.forEach(function (row, i) {
      var tag = row.querySelector(".idx");
      if (!tag) {
        tag = document.createElement("span");
        tag.className = "idx";
        row.insertBefore(tag, row.firstChild);
      }
      tag.textContent = prefix + (IDX_NUMS[i] || (i + 1));
    });
  }

  // r5：数量上限 → 添加按钮自动禁用（教育 3 / 实习 2 / JD 5）
  var SEC_MAX = { edu: 3, int: 2, job: 5 };

  function updateAddBtns() {
    Object.keys(SEC_MAX).forEach(function (sec) {
      var btn = document.querySelector('[data-add="' + sec + '"]');
      if (!btn) return;
      var n = $id(sec + "-rows").querySelectorAll(".row").length;
      btn.disabled = n >= SEC_MAX[sec];
    });
  }

  function addRow(sec, data) {
    var box = $id(sec + "-rows");
    var div = document.createElement("div");
    div.className = "row";
    div.innerHTML = ROW_TMPL[sec];
    var rm = document.createElement("span");
    rm.className = "rm";
    rm.textContent = "移除";
    rm.addEventListener("click", function () {
      box.removeChild(div);
      reindex(sec);
      updateAddBtns();
    });
    div.appendChild(rm);
    // 下拉选项
    if (sec === "edu") {
      var sel = div.querySelector(".in-degree");
      DEGREES.forEach(function (d) {
        var o = document.createElement("option");
        o.value = d[1]; o.textContent = d[0];
        sel.appendChild(o);
      });
      if (data && data.degree) sel.value = data.degree;
    }
    if (sec === "skill") {
      var sel2 = div.querySelector(".in-category");
      CATS.forEach(function (c) {
        var o = document.createElement("option");
        o.value = c[1]; o.textContent = c[0];
        sel2.appendChild(o);
      });
      if (data && data.category) sel2.value = data.category;
    }
    // 回填
    if (data) {
      var fields = { school: "in-school", major: "in-major", company: "in-company", position: "in-position",
        name: "in-name", role: "in-role", org: "in-org", time: "in-time", title: "in-title" };
      Object.keys(fields).forEach(function (f) {
        if (data[f] != null) div.querySelector("." + fields[f]).value = data[f];
      });
      var st = div.querySelector(".in-start"); if (st && data.startMonth) st.value = monthToInput(data.startMonth);
      var en = div.querySelector(".in-end"); if (en && data.endMonth) en.value = monthToInput(data.endMonth);
      var stack = div.querySelector(".in-stack"); if (stack && data.techStack) stack.value = (data.techStack || []).join("、");
      var duties = div.querySelector(".in-duties");
      if (duties && data.duties) duties.value = (data.duties || []).map(function (d) { return d.text; }).join("\n");
      var items = div.querySelector(".in-items");
      if (items && data.items) items.value = (data.items || []).map(function (i) { return i.text; }).join("\n");
      var jd = div.querySelector(".in-jd"); if (jd && data.jdText) jd.value = data.jdText;
    }
    box.appendChild(div);
    reindex(sec);
    updateAddBtns();
  }

  function collectRows(sec) {
    var box = $id(sec + "-rows");
    var out = [];
    box.querySelectorAll(".row").forEach(function (row) {
      var q = function (cls) { var el = row.querySelector("." + cls); return el ? el.value.trim() : ""; };
      var item = {};
      if (sec === "edu") {
        item = { school: q("in-school"), major: q("in-major"), degree: q("in-degree"),
                 startMonth: inputToMonth(q("in-start")), endMonth: inputToMonth(q("in-end")) };
        if (!item.school) return;
      } else if (sec === "int") {
        item = { company: q("in-company"), position: q("in-position"),
                 startMonth: inputToMonth(q("in-start")), endMonth: inputToMonth(q("in-end")),
                 duties: paragraphs(row.querySelector(".in-duties").value).map(function (t) { return { text: t }; }) };
        if (!item.company) return;
      } else if (sec === "proj") {
        item = { name: q("in-name"), role: q("in-role"),
                 startMonth: inputToMonth(q("in-start")), endMonth: inputToMonth(q("in-end")),
                 techStack: list(row.querySelector(".in-stack").value),
                 items: paragraphs(row.querySelector(".in-items").value).map(function (t) { return { text: t }; }) };
        if (!item.name) return;
      } else if (sec === "skill") {
        item = { category: q("in-category"), name: q("in-name") };
        if (!item.name) return;
      } else if (sec === "honor") {
        item = { name: q("in-name"), org: q("in-org") || null, time: q("in-time") || null };
        if (!item.name) return;
      } else if (sec === "job") {
        item = { title: q("in-title"), jdText: row.querySelector(".in-jd").value.trim() };
        if (!item.title || !item.jdText) return;
      }
      out.push(item);
    });
    return out;
  }

  function fillForm(r) {
    clearForm();
    var b = r.basicInfo || {};
    $id("f-name").value = b.name || ""; $id("f-age").value = b.age || "";
    $id("f-email").value = b.email || ""; $id("f-phone").value = b.phone || "";
    $id("f-website").value = b.website || ""; $id("f-base").value = b.base || "";
    $id("f-duration").value = b.internshipDuration || ""; $id("f-start").value = b.startAvailable || "";
    (r.education || []).forEach(function (e) { addRow("edu", e); });
    (r.internship || []).forEach(function (i) { addRow("int", i); });
    (r.project || []).forEach(function (p) { addRow("proj", p); });
    (r.skill || []).forEach(function (s) { addRow("skill", s); });
    (r.honor || []).forEach(function (h) { addRow("honor", h); });
    (r.jobs || []).forEach(function (j) { addRow("job", j); });
  }

  function collectForm() {
    var r = {
      basicInfo: {
        name: $id("f-name").value.trim(), age: parseInt($id("f-age").value, 10) || null,
        email: $id("f-email").value.trim(), phone: $id("f-phone").value.trim(),
        website: $id("f-website").value.trim() || null, base: $id("f-base").value.trim() || null,
        internshipDuration: $id("f-duration").value.trim() || null,
        startAvailable: $id("f-start").value.trim() || null,
      },
      education: collectRows("edu"),
      internship: collectRows("int"),
      project: collectRows("proj"),
      skill: collectRows("skill"),
      honor: collectRows("honor"),
      jobs: collectRows("job"),
    };
    return r;
  }

  function clearForm() {
    ["f-name", "f-age", "f-email", "f-phone", "f-website", "f-base", "f-duration", "f-start"].forEach(function (id) {
      $id(id).value = "";
    });
    ["edu", "int", "proj", "skill", "honor", "job"].forEach(function (sec) {
      $id(sec + "-rows").innerHTML = "";
    });
    updateAddBtns();
  }

  /* ---------------- 保存 ---------------- */
  function saveResume() {
    var body = collectForm();
    var status = $id("save-status");
    var req;
    if (state.resumeId) {
      body.id = state.resumeId;
      // 保留表单未覆盖的生成态字段（页面密度/方向/内容计划/生成追溯/照片等）
      var old = state.resume || {};
      body.createdAt = old.createdAt || body.createdAt;
      body.pageOption = old.pageOption || "one-page";
      body.density = old.density || "normal";
      body.direction = old.direction || null;
      body.contentPlan = old.contentPlan || null;
      body.generation = old.generation || null;
      body.photo = old.photo || null;
      body.version = old.version || "intern-version";
      body.identity = old.identity || "intern";
      req = fetch("/api/resume/" + state.resumeId, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) { return r.json(); });
    } else {
      req = fetch("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) { return r.json(); });
    }
    req.then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      if (!state.resumeId) {
        state.resumeId = j.data.resumeId;
      }
      $id("cur-resume").textContent = $id("f-name").value.trim() || "未命名";
      status.textContent = "已保存 " + new Date().toLocaleTimeString();
      $id("btn-generate").disabled = false;
      loadList();
    }).catch(function (e) {
      status.textContent = "保存失败：" + e.message;
    });
  }

  /* ---------------- 设置控制台（多 Provider / 搜索 / 插件默认值） ---------------- */
  var editingProviderId = null;

  function settingsText() {
    var st = $id("settings-status");
    if (state.activeProvider) {
      st.textContent = "已激活：" + state.activeProvider.name;
      st.className = "key-status ok";
    } else if (state.hasAnyProvider) {
      st.textContent = "未启用任何配置";
      st.className = "key-status warn";
    } else {
      st.textContent = "未配置模型 Key";
      st.className = "key-status warn";
    }
  }

  function renderProviders(providers, activeId) {
    var box = $id("prov-list");
    box.innerHTML = "";
    state.providers = providers || [];
    state.activeProviderId = activeId || "";
    if (!state.providers.length) {
      box.innerHTML = '<div class="muted small">暂无配置：在下方「新增配置」或上方卡片中填写后保存。</div>';
      settingsText();
      return;
    }
    state.providers.forEach(function (p) {
      var item = document.createElement("div");
      item.className = "prov-item" + (p.id === activeId ? " active" : "");
      var head = document.createElement("div");
      head.className = "prov-main";
      var tag = p.enabled
        ? (p.id === activeId ? '<span class="tag required">已激活</span>' : '<span class="tag ok-tag">启用</span>')
        : '<span class="tag optional">停用</span>';
      head.innerHTML = "<b>" + esc(p.name || "未命名") + "</b>" +
        " · 模型 <code>" + esc(p.model || "-") + "</code> " + tag;
      var sub = document.createElement("div");
      sub.className = "prov-sub";
      sub.textContent = "Key: " + (p.apiKeyMasked || "未设置") + " · " + (p.baseUrl || "");
      var ops = document.createElement("div");
      ops.className = "prov-ops";
      if (p.id !== activeId) {
        var act = document.createElement("button");
        act.className = "btn tiny";
        act.textContent = "激活";
        act.addEventListener("click", function () { activateProvider(p.id); });
        ops.appendChild(act);
      }
      var edit = document.createElement("button");
      edit.className = "btn tiny";
      edit.textContent = "编辑";
      edit.addEventListener("click", function () { editProvider(p); });
      ops.appendChild(edit);
      var del = document.createElement("button");
      del.className = "btn tiny danger-t";
      del.textContent = "删除";
      del.addEventListener("click", function () { delProvider(p.id); });
      ops.appendChild(del);
      item.appendChild(head);
      item.appendChild(sub);
      item.appendChild(ops);
      box.appendChild(item);
    });
    // 激活项排最前（优先级）
    var act = state.providers.filter(function (p) { return p.id === activeId; })[0];
    if (act) {
      state.activeProvider = act;
      // 快速配置组展示激活项
      $id("s-name").value = act.name || "";
      $id("s-model").value = act.model || "";
      $id("s-baseurl").value = act.baseUrl || "";
    } else {
      state.activeProvider = null;
    }
    state.hasAnyProvider = state.providers.length > 0;
    settingsText();
  }

  function loadSettings() {
    return fetch("/api/settings").then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      var d = j.data;
      renderProviders(d.providers || [], d.activeProviderId);
      $id("s-deep").checked = d.deepSearchDefault !== false;
      $id("s-watermark-formal").checked = d.watermarkDefault !== "practice";
      // 同步生成条默认值
      $id("g-deep").checked = d.deepSearchDefault !== false;
      $id("g-watermark").value = d.watermarkDefault === "practice" ? "practice" : "formal";
      if (d.searchHasKey) {
        $id("search-msg").textContent = "搜索 Key 已配置：" + d.searchApiKeyMasked;
      }
    }).catch(function () {});
  }

  function testProvider(baseUrl, model, apiKey) {
    return fetch("/api/settings/providers/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baseUrl: baseUrl, model: model, apiKey: apiKey }),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      return j.data.ok ? "自检通过" : "自检失败：" + (j.data.error || "未知错误");
    });
  }

  // 保存并自检（快速配置组 = 新增或编辑当前 provider）
  function saveSettings() {
    var hint = $id("settings-hint");
    var body = {
      name: $id("s-name").value.trim(),
      baseUrl: $id("s-baseurl").value.trim(),
      model: $id("s-model").value.trim(),
    };
    if (editingProviderId) body.id = editingProviderId;
    var key = $id("s-apikey").value.trim();
    if (key) body.apiKey = key;
    if (!body.name || !body.baseUrl || !body.model) {
      hint.textContent = "请填写配置名称 / Base URL / 模型名";
      return;
    }
    $id("btn-save-settings").disabled = true;
    hint.textContent = "保存中…";
    fetch("/api/settings/providers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      var saved = (j.data.providers || []).filter(function (p) { return p.id === j.data.activeProviderId; })[0] ||
                  (j.data.providers || [])[0];
      renderProviders(j.data.providers, j.data.activeProviderId);
      $id("s-apikey").value = "";
      editingProviderId = null;
      $id("btn-save-settings").textContent = "保存并自检";
      if (key && saved) {
        return testProvider(saved.baseUrl, saved.model, key).then(function (msg) {
          hint.textContent = "已保存，" + msg;
        });
      }
      hint.textContent = "已保存 " + new Date().toLocaleTimeString();
      return Promise.resolve();
    }).catch(function (e) {
      hint.textContent = "保存失败：" + e.message;
    }).then(function () {
      $id("btn-save-settings").disabled = false;
    });
  }

  // 高级设置：新增配置
  function addProvider() {
    var msg = $id("prov-msg");
    var body = {
      name: $id("p-name").value.trim(),
      baseUrl: $id("p-baseurl").value.trim(),
      model: $id("p-model").value.trim(),
      capabilities: $id("p-cap").value.trim() || "text",
    };
    var key = $id("p-apikey").value.trim();
    if (!body.name || !body.baseUrl || !body.model) {
      msg.textContent = "请填写配置名称 / Base URL / 模型名";
      return;
    }
    if (key) body.apiKey = key;
    $id("btn-add-provider").disabled = true;
    msg.textContent = "保存中…";
    fetch("/api/settings/providers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      renderProviders(j.data.providers, j.data.activeProviderId);
      var saved = (j.data.providers || []).filter(function (p) { return p.id === j.data.activeProviderId; })[0];
      ["p-name", "p-baseurl", "p-model", "p-apikey"].forEach(function (id) { $id(id).value = ""; });
      if (key && saved) {
        return testProvider(saved.baseUrl, saved.model, key).then(function (m) { msg.textContent = m; });
      }
      msg.textContent = "已添加配置";
      return Promise.resolve();
    }).catch(function (e) {
      msg.textContent = "添加失败：" + e.message;
    }).then(function () {
      $id("btn-add-provider").disabled = false;
    });
  }

  // 高级设置：用当前卡片字段自检（不落盘）
  function testQuickProvider() {
    var msg = $id("prov-msg");
    var baseUrl = $id("s-baseurl").value.trim() || $id("p-baseurl").value.trim();
    var model = $id("s-model").value.trim() || $id("p-model").value.trim();
    var key = $id("s-apikey").value.trim() || $id("p-apikey").value.trim();
    if (!baseUrl || !model || !key) { msg.textContent = "请填写 Base URL / 模型 / API Key"; return; }
    msg.textContent = "自检中…";
    testProvider(baseUrl, model, key).then(function (m) { msg.textContent = m; });
  }

  function editProvider(p) {
    editingProviderId = p.id;
    $id("s-name").value = p.name || "";
    $id("s-model").value = p.model || "";
    $id("s-baseurl").value = p.baseUrl || "";
    $id("s-apikey").value = "";
    $id("btn-save-settings").textContent = "保存修改";
    $id("settings-hint").textContent = "正在编辑：" + p.name + "（Key 留空 = 保留原 Key）";
    $id("adv-settings").removeAttribute("open");
    $id("btn-save-settings").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function activateProvider(id) {
    fetch("/api/settings/providers/" + id + "/activate", { method: "POST" })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (j.code !== 0) throw new Error(errMsg(j));
        renderProviders(j.data.providers, j.data.activeProviderId);
      }).catch(function (e) { Adapt.showBanner("激活失败：" + e.message, true); });
  }

  function delProvider(id) {
    fetch("/api/settings/providers/" + id, { method: "DELETE" })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (j.code !== 0) throw new Error(errMsg(j));
        if (editingProviderId === id) {
          editingProviderId = null;
          $id("btn-save-settings").textContent = "保存并自检";
        }
        renderProviders(j.data.providers, j.data.activeProviderId);
      }).catch(function (e) { Adapt.showBanner("删除失败：" + e.message, true); });
  }

  function saveSearch() {
    var key = $id("s-searchkey").value.trim();
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ searchApiKey: key }),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      $id("s-searchkey").value = "";
      $id("search-msg").textContent = key ? "已保存搜索 Key" : "已关闭联网搜索";
    }).catch(function (e) {
      $id("search-msg").textContent = "保存失败：" + e.message;
    });
  }

  function saveDefaults() {
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        deepSearchDefault: $id("s-deep").checked,
        watermarkDefault: $id("s-watermark-formal").checked ? "formal" : "practice",
      }),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      $id("g-deep").checked = $id("s-deep").checked;
      $id("g-watermark").value = $id("s-watermark-formal").checked ? "formal" : "practice";
      $id("defaults-msg").textContent = "已保存默认值 " + new Date().toLocaleTimeString();
    }).catch(function (e) {
      $id("defaults-msg").textContent = "保存失败：" + e.message;
    });
  }

  /* ---------------- 生成 + SSE ---------------- */
  function startGenerate() {
    if (!state.resumeId) { Adapt.showBanner("请先保存简历", true); return; }
    var body = {
      resumeId: state.resumeId,
      pageOption: $id("g-page").value,
      watermarkMode: $id("g-watermark").value,
      deepSearch: $id("g-deep").checked,
    };
    $id("btn-generate").disabled = true;
    $id("btn-cancel").disabled = false;
    $id("progress").classList.remove("hidden");
    $id("progress-fill").style.width = "0%";
    $id("progress-text").textContent = "提交中…";
    Adapt.showBanner("生成中…");
    fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) throw new Error(errMsg(j));
      state.taskId = j.data.taskId;
      openSSE(state.taskId);
    }).catch(function (e) {
      Adapt.showBanner("提交失败：" + e.message, true);
      resetGenerateBtns();
    });
  }

  function openSSE(taskId) {
    closeSSE();
    var curStage = 0, curStageTotal = 1;
    es = new EventSource("/api/task/" + taskId + "/events");
    es.addEventListener("task.stage", function (ev) {
      var d = JSON.parse(ev.data);
      curStage = d.stageIndex || 0;
      curStageTotal = d.stageTotal || 1;
      setProgress((curStage - 1) / curStageTotal, "阶段：" + d.stage + "（" + curStage + "/" + curStageTotal + "）");
    });
    es.addEventListener("block.progress", function (ev) {
      var d = JSON.parse(ev.data);
      setProgress((curStage - 1) / curStageTotal + (d.progress || 0) / curStageTotal,
        "生成板块：" + (d.block || ""));
    });
    es.addEventListener("block.done", function (ev) {
      var d = JSON.parse(ev.data);
      $id("progress-text").textContent = "板块完成：" + (d.block || "") + (d.degraded ? "（降级）" : "");
    });
    es.addEventListener("task.done", function (ev) {
      var d = JSON.parse(ev.data);
      closeSSE();
      state.html = d.html;
      state.config = d.config;
      state.resumeId = d.resumeId || state.resumeId;
      fetch("/api/resume/" + state.resumeId).then(function (r) { return r.json(); }).then(function (j) {
        if (j.code === 0) state.resume = j.data;
      }).catch(function () {}).then(function () {
        Adapt.render(state.html);
        $id("btn-adapt").disabled = false;
        $id("btn-export").disabled = false;
        $id("btn-generate").disabled = false;
        $id("btn-cancel").disabled = true;
        $id("progress-fill").style.width = "100%";
        $id("progress-text").textContent = "生成完成，可预览 / 适配 / 编辑";
        Adapt.showBanner("生成完成。请预览确认内容与排版；如需调整可点击正文编辑，或使用「自动适配」。");
        loadList();
      });
    });
    es.addEventListener("task.failed", function (ev) {
      var d = JSON.parse(ev.data);
      closeSSE();
      Adapt.showBanner("生成失败：" + (d.error || d.message || "未知错误"), true);
      resetGenerateBtns();
    });
    es.addEventListener("task.canceled", function () {
      closeSSE();
      Adapt.showBanner("任务已取消");
      resetGenerateBtns();
    });
    es.onerror = function () {
      // 终态后服务端会断开；若已有结果则忽略
      if (es && es.readyState === EventSource.CLOSED) closeSSE();
    };
  }

  function setProgress(p, text) {
    $id("progress-fill").style.width = Math.max(0, Math.min(100, Math.round(p * 100))) + "%";
    $id("progress-text").textContent = text;
  }

  function cancelTask() {
    if (!state.taskId) return;
    fetch("/api/task/" + state.taskId + "/cancel", { method: "POST" })
      .then(function (r) { return r.json(); }).catch(function () {});
  }

  function closeSSE() { if (es) { es.close(); es = null; } }
  function resetGenerateBtns() {
    $id("btn-generate").disabled = false;
    $id("btn-cancel").disabled = true;
  }

  /* ---------------- 初始化 ---------------- */
  function init() {
    health();
    loadSettings();
    loadList();
    $id("btn-new").addEventListener("click", newResume);
    $id("btn-save").addEventListener("click", saveResume);
    $id("btn-save-settings").addEventListener("click", saveSettings);
    $id("btn-add-provider").addEventListener("click", addProvider);
    $id("btn-test-provider").addEventListener("click", testQuickProvider);
    $id("btn-save-search").addEventListener("click", saveSearch);
    $id("btn-save-defaults").addEventListener("click", saveDefaults);
    $id("btn-generate").addEventListener("click", startGenerate);
    $id("btn-cancel").addEventListener("click", cancelTask);
    document.querySelectorAll("[data-add]").forEach(function (btn) {
      btn.addEventListener("click", function () { addRow(btn.getAttribute("data-add")); });
    });
    updateAddBtns();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

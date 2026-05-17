"""HTML rendering for the Kompany web dashboard spike."""

from __future__ import annotations

from html import escape
from typing import Any


def render_dashboard(snapshot: dict[str, Any]) -> str:
    company = snapshot.get("company", {})
    runtime = snapshot.get("runtime", {})
    finance = snapshot.get("finance", {})
    approvals = snapshot.get("approvals", {})
    projects = snapshot.get("projects", {})
    agents = snapshot.get("agents", {})
    office = snapshot.get("office", {})
    blockers = office.get("blockers", []) or approvals.get("blockers", []) or []

    rooms_html = "".join(_render_room(room) for room in office.get("rooms", []))
    blockers_html = _render_blockers(blockers)
    runtime_state = runtime.get("state", "unknown")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kompany Dashboard</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif; background: radial-gradient(circle at top left, #26345f, #10131a 42%, #080a10 100%); color: #f6f7fb; }}
    header {{ padding: 36px 32px; background: linear-gradient(135deg, rgba(120, 255, 214, .18), rgba(128, 216, 255, .08)); border-bottom: 1px solid rgba(168, 179, 207, .18); }}
    main {{ padding: 24px; display: grid; gap: 20px; }}
    .command-center {{ display: grid; grid-template-columns: 1fr auto; gap: 24px; align-items: end; }}
    .command-title {{ margin: 8px 0; font-size: clamp(32px, 6vw, 64px); line-height: .95; }}
    .command-briefing {{ max-width: 820px; color: #dbe5ff; font-size: 18px; }}
    .signal-panel {{ min-width: 220px; background: rgba(16, 19, 26, .72); border: 1px solid rgba(120, 255, 214, .24); border-radius: 20px; padding: 18px; box-shadow: 0 20px 50px rgba(0, 0, 0, .26); }}
    .signal-light {{ display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 999px; background: rgba(120, 255, 214, .12); color: #78ffd6; font-weight: 700; }}
    .signal-light::before {{ content: ""; width: 10px; height: 10px; border-radius: 50%; background: #78ffd6; box-shadow: 0 0 18px #78ffd6; }}
    .logout-link {{ display: inline-block; margin-top: 12px; color: #ffd6a5; font-size: 13px; }}
    .hud {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }}
    .hud-card, .alert-board, .room, .link-console, .action-console {{ background: rgba(24, 29, 41, .88); border: 1px solid #2a3347; border-radius: 18px; padding: 18px; box-shadow: 0 16px 44px rgba(0, 0, 0, .22); }}
    .hud-card {{ position: relative; overflow: hidden; }}
    .hud-card::after {{ content: ""; position: absolute; inset: auto 14px 12px 14px; height: 2px; background: linear-gradient(90deg, #78ffd6, transparent); opacity: .55; }}
    .label {{ color: #a8b3cf; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .value {{ margin-top: 8px; font-size: 26px; font-weight: 800; }}
    .floor-heading {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    .floor-heading h2 {{ margin: 0; font-size: 24px; }}
    .office {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
    .room {{ border-top: 3px solid #80d8ff; }}
    .room h2 {{ margin: 0 0 8px; }}
    .room-purpose {{ color: #c7d2ef; margin: 0 0 16px; }}
    .characters {{ display: grid; gap: 10px; padding: 0; list-style: none; }}
    .agent-card {{ display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: center; padding: 10px; border-radius: 14px; background: #20283a; border: 1px solid #3a4b6d; }}
    .agent-avatar {{ display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px; background: linear-gradient(135deg, #78ffd6, #80d8ff); color: #10131a; font-weight: 900; text-transform: uppercase; }}
    .agent-role {{ font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }}
    .agent-task {{ color: #a8b3cf; font-size: 13px; margin-top: 2px; }}
    .status {{ color: #78ffd6; font-size: 13px; }}
    .alert-list {{ margin: 10px 0 0; padding-left: 18px; }}
    .alert-list li {{ margin: 8px 0; color: #ffd6a5; }}
    .action-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-top: 14px; }}
    .action-button {{ border: 1px solid #3a4b6d; border-radius: 14px; padding: 12px; background: #20283a; color: #f6f7fb; font: inherit; font-weight: 800; cursor: pointer; text-align: left; }}
    .action-button:hover {{ border-color: #78ffd6; color: #78ffd6; }}
    .action-result {{ margin: 14px 0 0; padding: 14px; min-height: 72px; overflow: auto; border-radius: 14px; background: #10131a; border: 1px solid #2a3347; color: #c7d2ef; white-space: pre-wrap; font: 13px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    .refresh-status {{ color: #a8b3cf; font-size: 13px; margin-top: 12px; }}
    a {{ color: #80d8ff; }}
    @media (max-width: 720px) {{ .command-center {{ grid-template-columns: 1fr; }} .signal-panel {{ min-width: 0; }} }}
  </style>
</head>
<body>
  <header class="command-center">
    <div>
      <div class="label">Kompany RPG Command Center</div>
      <h1 class="command-title">{escape(company.get('name') or 'Kompany')}</h1>
      <p class="command-briefing">{escape(company.get('goal') or 'No goal configured')}</p>
    </div>
    <aside class="signal-panel" aria-label="Runtime signal">
      <div class="label">Runtime signal</div>
      <div class="signal-light">{escape(str(runtime_state))}</div>
      <div class="refresh-status">Stage: {escape(str(company.get('stage') or 'unknown'))}</div>
      <a class="logout-link" href="/dashboard/logout">Log out dashboard session</a>
    </aside>
  </header>
  <main>
    <section id="metrics" class="hud" aria-label="Status HUD">
      {_metric('Runtime', runtime.get('state', 'unknown'), 'runtime')}
      {_metric('Stage', company.get('stage', 'unknown'), 'stage')}
      {_metric('Balance', f"€{finance.get('balance', 0):.2f}", 'balance')}
      {_metric('AI Cost', f"${finance.get('total_ai_costs', 0):.4f}", 'ai-cost')}
      {_metric('Pending approvals', approvals.get('pending', 0), 'pending-approvals')}
      {_metric('Active projects', projects.get('active', 0), 'active-projects')}
      {_metric('Active agents', f"{agents.get('active', 0)} / {agents.get('total', 0)}", 'active-agents')}
    </section>
    <section class="alert-board">
      <div class="label">Quest blockers</div>
      <ul id="blockers" class="alert-list">{blockers_html}</ul>
    </section>
    <section class="action-console" aria-label="RPG action console">
      <div class="label">RPG action console</div>
      <h2>Command safe operations</h2>
      <div class="action-grid">
        <button class="action-button" data-dashboard-action="runtime-status" type="button">Inspect runtime signal</button>
        <button class="action-button" data-dashboard-action="heartbeat" type="button">Run heartbeat scan</button>
        <button class="action-button" data-dashboard-action="approvals" type="button">Check approval queue</button>
        <button class="action-button" data-dashboard-action="replay-cleanup" type="button">Clean replay cache</button>
        <button class="action-button" data-dashboard-action="approve" type="button">Approve request</button>
        <button class="action-button" data-dashboard-action="reject" type="button">Reject request</button>
        <button class="action-button" data-dashboard-action="runtime-suspend" type="button">Suspend runtime</button>
        <button class="action-button" data-dashboard-action="runtime-resume" type="button">Resume runtime</button>
      </div>
      <pre id="action-result" class="action-result">Choose a safe RPG action.</pre>
    </section>
    <section>
      <div class="floor-heading">
        <div>
          <div class="label">Virtual company floor</div>
          <h2>Rooms, agents, and active quests</h2>
        </div>
        <div class="label">Live RPG view</div>
      </div>
      <div id="office" class="office">{rooms_html}</div>
    </section>
    <section class="link-console">
      <a href="/observability">View raw observability JSON</a>
      <div id="refresh-status" class="refresh-status">Live refresh enabled</div>
    </section>
  </main>
  <script>
    const refreshStatus = document.getElementById('refresh-status');
    const actionResult = document.getElementById('action-result');
    const metrics = {{
      runtime: document.querySelector('[data-metric="runtime"] .value'),
      stage: document.querySelector('[data-metric="stage"] .value'),
      balance: document.querySelector('[data-metric="balance"] .value'),
      aiCost: document.querySelector('[data-metric="ai-cost"] .value'),
      pendingApprovals: document.querySelector('[data-metric="pending-approvals"] .value'),
      activeProjects: document.querySelector('[data-metric="active-projects"] .value'),
      activeAgents: document.querySelector('[data-metric="active-agents"] .value'),
    }};

    function setText(node, value) {{
      if (node) node.textContent = String(value ?? '');
    }}

    function money(prefix, value, digits) {{
      return `${{prefix}}${{Number(value || 0).toFixed(digits)}}`;
    }}

    function appendBlocker(list, blocker) {{
      const item = document.createElement('li');
      item.textContent = `${{blocker.kind || 'blocker'}}: ${{blocker.summary || blocker.id || ''}}`;
      list.appendChild(item);
    }}

    function renderBlockers(snapshot) {{
      const list = document.getElementById('blockers');
      if (!list) return;
      list.replaceChildren();
      const office = snapshot.office || {{}};
      const approvals = snapshot.approvals || {{}};
      const blockers = office.blockers || approvals.blockers || [];
      if (!blockers.length) {{
        const item = document.createElement('li');
        item.textContent = 'No blockers';
        list.appendChild(item);
        return;
      }}
      blockers.forEach((blocker) => appendBlocker(list, blocker || {{}}));
    }}

    function renderAgentCard(character) {{
      const item = document.createElement('li');
      item.className = 'agent-card';

      const avatar = document.createElement('div');
      avatar.className = 'agent-avatar';
      avatar.textContent = String(character.role || 'agent').slice(0, 2);
      item.appendChild(avatar);

      const details = document.createElement('div');
      const role = document.createElement('div');
      role.className = 'agent-role';
      role.textContent = character.role || 'agent';
      details.appendChild(role);

      const status = document.createElement('div');
      status.className = 'status';
      status.textContent = character.status || 'idle';
      details.appendChild(status);

      const task = document.createElement('div');
      task.className = 'agent-task';
      task.textContent = character.current_task || 'Awaiting quest';
      details.appendChild(task);

      item.appendChild(details);
      return item;
    }}

    function renderRooms(snapshot) {{
      const office = document.getElementById('office');
      if (!office) return;
      office.replaceChildren();
      const rooms = ((snapshot.office || {{}}).rooms || []);
      rooms.forEach((room) => {{
        const article = document.createElement('article');
        article.className = 'room';

        const title = document.createElement('h2');
        title.textContent = room.name || 'room';
        article.appendChild(title);

        const purpose = document.createElement('p');
        purpose.className = 'room-purpose';
        purpose.textContent = room.purpose || '';
        article.appendChild(purpose);

        const characters = document.createElement('ul');
        characters.className = 'characters';
        const roomCharacters = room.characters || [];
        if (!roomCharacters.length) {{
          const empty = document.createElement('li');
          empty.className = 'agent-card';
          empty.textContent = 'empty room';
          characters.appendChild(empty);
        }}
        roomCharacters.forEach((character) => {{
          characters.appendChild(renderAgentCard(character || {{}}));
        }});
        article.appendChild(characters);
        office.appendChild(article);
      }});
    }}

    function renderSnapshot(snapshot) {{
      const company = snapshot.company || {{}};
      const runtime = snapshot.runtime || {{}};
      const finance = snapshot.finance || {{}};
      const approvals = snapshot.approvals || {{}};
      const projects = snapshot.projects || {{}};
      const agents = snapshot.agents || {{}};
      const pendingApproval = (approvals.items || []).find((item) => item && item.status === 'pending') || null;
      if (pendingApproval) {{
        actionTargets.approvalId = pendingApproval.id || null;
      }}

      setText(metrics.runtime, runtime.state || 'unknown');
      setText(metrics.stage, company.stage || 'unknown');
      setText(metrics.balance, money('€', finance.balance, 2));
      setText(metrics.aiCost, money('$', finance.total_ai_costs, 4));
      setText(metrics.pendingApprovals, approvals.pending || 0);
      setText(metrics.activeProjects, projects.active || 0);
      setText(metrics.activeAgents, `${{agents.active || 0}} / ${{agents.total || 0}}`);
      renderBlockers(snapshot);
      renderRooms(snapshot);
    }}

    async function refreshDashboard() {{
      try {{
        const response = await fetch('/observability', {{headers: {{Accept: 'application/json'}}}});
        if (!response.ok) throw new Error('refresh failed');
        renderSnapshot(await response.json());
        refreshStatus.textContent = `Live refresh updated ${{new Date().toLocaleTimeString()}}`;
      }} catch (error) {{
        refreshStatus.textContent = 'Live refresh stale';
      }}
    }}

    async function runDashboardAction(action) {{
      if (actionResult) actionResult.textContent = `Running ${{action}}...`;
      const payload = {{action}};
      if (action === 'approve' || action === 'reject') {{
        payload.approval_id = actionTargets.approvalId || prompt('Approval ID:');
        if (!payload.approval_id) {{
          if (actionResult) actionResult.textContent = 'Action cancelled';
          return;
        }}
        actionTargets.approvalId = payload.approval_id;
      }}
      if (action === 'reject' || action === 'runtime-suspend') {{
        payload.reason = prompt('Reason:', actionTargets.reason) || actionTargets.reason;
        actionTargets.reason = payload.reason;
      }}
      try {{
        const response = await fetch('/dashboard/action', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{'Content-Type': 'application/json', Accept: 'application/json'}},
          body: JSON.stringify(payload),
        }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'action failed');
        if (actionResult) actionResult.textContent = JSON.stringify(payload, null, 2);
        await refreshDashboard();
      }} catch (error) {{
        if (actionResult) actionResult.textContent = `Action failed: ${{error.message}}`;
      }}
    }}

    document.querySelectorAll('[data-dashboard-action]').forEach((button) => {{
      button.addEventListener('click', () => runDashboardAction(button.dataset.dashboardAction));
    }});

    setInterval(refreshDashboard, 5000);
    refreshDashboard();
  </script>
</body>
</html>"""


def _metric(label: str, value: Any, key: str) -> str:
    return (
        f"<article class=\"hud-card\" data-metric=\"{escape(key)}\">"
        f"<div class=\"label\">{escape(str(label))}</div>"
        f"<div class=\"value\">{escape(str(value))}</div>"
        "</article>"
    )


def _render_blockers(blockers: list[dict[str, Any]]) -> str:
    return "".join(
        f"<li>{escape(blocker.get('kind', 'blocker'))}: "
        f"{escape(blocker.get('summary') or blocker.get('id') or '')}</li>"
        for blocker in blockers
    ) or "<li>No blockers</li>"


def _render_character(character: dict[str, Any]) -> str:
    role = str(character.get("role") or "agent")
    status = str(character.get("status") or "idle")
    current_task = str(character.get("current_task") or "Awaiting quest")
    avatar = role[:2]
    return (
        "<li class=\"agent-card\">"
        f"<div class=\"agent-avatar\">{escape(avatar)}</div>"
        "<div>"
        f"<div class=\"agent-role\">{escape(role)}</div>"
        f"<div class=\"status\">{escape(status)}</div>"
        f"<div class=\"agent-task\">{escape(current_task)}</div>"
        "</div>"
        "</li>"
    )


def _render_room(room: dict[str, Any]) -> str:
    characters = "".join(
        _render_character(character)
        for character in room.get("characters", [])
    ) or "<li class=\"agent-card\">empty room</li>"
    return (
        "<article class=\"room\">"
        f"<h2>{escape(room.get('name', 'room'))}</h2>"
        f"<p class=\"room-purpose\">{escape(room.get('purpose', ''))}</p>"
        f"<ul class=\"characters\">{characters}</ul>"
        "</article>"
    )

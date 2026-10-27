"""Server-rendered HTML for fake-miira. Plain, dev-only, Miira-ish colours."""

import json
from collections.abc import Iterable
from html import escape
from typing import Any

from .store import MiiraUser, VibeFile

CSS = """
:root{--bg:#f6f5f2;--fg:#1d1b19;--muted:#6b6560;--card:#fff;--line:#e4e0da;--accent:#5b3df5}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:12px;align-items:center;padding:10px 20px;background:var(--card);border-bottom:1px solid var(--line)}
header b{font-size:18px}header .sp{flex:1}main{padding:20px;max-width:1100px;margin:0 auto}
button,.btn{font:inherit;border:1px solid var(--line);background:var(--card);border-radius:8px;padding:6px 12px;cursor:pointer;color:inherit;text-decoration:none}
.primary{background:var(--accent);border-color:var(--accent);color:#fff}
ul.list{list-style:none;padding:0;margin:0}ul.list li{display:flex;gap:10px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px}
.muted{color:var(--muted)}.tag{font-size:12px;border-radius:99px;background:var(--bg);padding:2px 8px}
dialog{border:1px solid var(--line);border-radius:12px;padding:20px;min-width:420px}
pre{background:#14121a;color:#e8e6f0;padding:12px;border-radius:8px;overflow:auto;max-height:50vh;font-size:12px}
iframe{width:100%;height:calc(100vh - 56px);border:0;display:block;background:#fff}
input,select,textarea{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px}
"""


def _layout(title: str, body: str, user: MiiraUser | None = None, full: bool = False) -> str:
    who = ""
    if user:
        who = (
            f'<span class="muted">{escape(user.name)} · {escape(user.role)}</span>'
            '<form method="post" action="/logout" style="margin:0"><button>Log ud</button></form>'
        )
    main = body if full else f"<main>{body}</main>"
    return (
        f"<!doctype html><html lang=da><meta charset=utf-8><title>{escape(title)} · fake-miira</title>"
        f"<style>{CSS}</style><header><a href='/' class=btn>fake-miira</a><b>{escape(title)}</b>"
        f"<span class=sp></span>{who}</header>{main}</html>"
    )


def login_page(params: dict[str, str], users: Iterable[MiiraUser], action: str) -> str:
    hidden = "".join(
        f'<input type=hidden name="{escape(k)}" value="{escape(v)}">'
        for k, v in params.items()
        if k in ("redirect_uri", "state", "nonce", "code_challenge", "client_id")
    )
    rows = "".join(
        f'<li><form method=post action="{action}" style="display:flex;gap:10px;align-items:center;width:100%;margin:0">'
        f'{hidden}<input type=hidden name=user_id value="{escape(u.id)}">'
        f'<b style="flex:1">{escape(u.name)}</b><span class=tag>{escape(u.role)}</span>'
        f"<button class=primary>Log ind</button></form></li>"
        for u in users
    )
    return _layout("Log ind", f"<p class=muted>Vælg en testbruger.</p><ul class=list>{rows}</ul>")


def drive_page(user: MiiraUser, files: list[tuple[VibeFile, str]]) -> str:
    rows = "".join(
        f'<li><b style="flex:1"><a href="/files/{escape(f.id)}">{escape(f.title)}</a></b>'
        f'<span class=tag>{escape(role)}</span><code class=muted>{escape(f.id)}</code></li>'
        for f, role in files
    ) or "<li class=muted>Ingen Vibes endnu.</li>"
    create = ""
    if user.role == "laerer":
        create = (
            "<p><button class=primary id=new>Ny Vibe</button></p><script>"
            "document.getElementById('new').onclick=async()=>{const t=prompt('Titel','Ny Vibe');if(t===null)return;"
            "const r=await fetch('/api/files',{method:'POST',headers:{'content-type':'application/json'},"
            "body:JSON.stringify({title:t})});const f=await r.json();location.href='/files/'+f.id}</script>"
        )
    admin = "<p><a class=btn href='/admin/vibe'>System admin · Vibe</a></p>" if user.role == "system-admin" else ""
    return _layout("Drev", f"{create}{admin}<ul class=list>{rows}</ul>", user)


def file_page(user: MiiraUser, f: VibeFile, role: str, studio_origin: str) -> str:
    cfg = json.dumps({"fileId": f.id, "studio": studio_origin, "role": role})
    body = f"""
<div style="display:flex;gap:8px;padding:8px 20px;align-items:center;background:var(--card);border-bottom:1px solid var(--line)">
  <b id=title>{escape(f.title)}</b><span class=tag>{escape(role)}</span><span style="flex:1"></span>
  <label class=muted>Tema <select id=theme><option>light</option><option>dark</option></select></label>
  <button id=share>Del</button>
</div>
<iframe id=vibe src="{escape(studio_origin)}/embed/{escape(f.id)}?chrome=none"
  allow="clipboard-write; fullscreen"
  sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads"></iframe>
<dialog id=dlg><h3 style="margin-top:0">Del “{escape(f.title)}”</h3><div id=shares></div>
  <p><select id=target></select> <select id=role><option value=viewer>kan se</option><option value=editor>kan redigere</option></select>
  <button id=add>Tilføj</button></p>
  <p style="text-align:right"><button id=cancel>Annuller</button> <button class=primary id=save>Gem</button></p></dialog>
<script>
const CFG={cfg};
const frame=document.getElementById('vibe');
const post=(m)=>frame.contentWindow.postMessage(m,CFG.studio);
const log=(...a)=>console.log('[fake-miira bridge]',...a);
window.addEventListener('message',async(e)=>{{
  if(e.origin!==CFG.studio||e.source!==frame.contentWindow)return;
  const m=e.data||{{}};log('<-',m);
  if(m.type==='drive:ready'){{
    const r=await fetch('/api/files/'+CFG.fileId+'/launch',{{method:'POST'}});
    const {{launchToken}}=await r.json();
    post({{type:'drive:init',fileId:CFG.fileId,launchToken,theme:document.getElementById('theme').value,locale:'da'}});
  }}else if(m.type==='drive:share'){{openShare();}}
  else if(m.type==='drive:title'&&m.title){{
    await fetch('/api/files/'+CFG.fileId,{{method:'PATCH',headers:{{'content-type':'application/json'}},body:JSON.stringify({{title:m.title}})}});
    document.getElementById('title').textContent=m.title;
  }}else if(m.type==='drive:close'){{location.href='/';}}
}});
document.getElementById('theme').onchange=(e)=>post({{type:'drive:theme',theme:e.target.value}});
const dlg=document.getElementById('dlg');let state=null;
const roleDa={{viewer:'kan se',editor:'kan redigere'}};
function render(){{
  const el=document.getElementById('shares');
  el.innerHTML=state.shares.length?'':'<p class=muted>Ikke delt endnu.</p>';
  state.shares.forEach((s,i)=>{{
    const all=[...state.candidates.users,...state.candidates.classes];
    const name=(all.find(x=>x.id===s.id)||{{name:s.id}}).name;
    const row=document.createElement('div');row.style.cssText='display:flex;gap:8px;align-items:center;margin:4px 0';
    row.innerHTML='<span style="flex:1"></span><span class=tag></span>';
    row.children[0].textContent=(s.kind==='class'?'Klasse ':'')+name;row.children[1].textContent=roleDa[s.role];
    if(state.canEdit){{const b=document.createElement('button');b.textContent='Fjern';b.onclick=()=>{{state.shares.splice(i,1);render();}};row.append(b);}}
    el.append(row);
  }});
  const t=document.getElementById('target');t.innerHTML='';
  state.candidates.classes.forEach(c=>t.add(new Option('Klasse '+c.name,'class:'+c.id)));
  state.candidates.users.forEach(u=>t.add(new Option(u.name,'user:'+u.id)));
  ['target','role','add','save'].forEach(id=>document.getElementById(id).disabled=!state.canEdit);
}}
async function openShare(){{state=await (await fetch('/api/files/'+CFG.fileId+'/shares')).json();render();dlg.showModal();}}
document.getElementById('share').onclick=openShare;
if(new URLSearchParams(location.search).get('share')==='1')openShare();
document.getElementById('cancel').onclick=()=>dlg.close();
document.getElementById('add').onclick=()=>{{
  const [kind,id]=document.getElementById('target').value.split(':');const role=document.getElementById('role').value;
  state.shares=state.shares.filter(s=>!(s.kind===kind&&s.id===id));state.shares.push({{kind,id,role}});render();
}};
document.getElementById('save').onclick=async()=>{{
  await fetch('/api/files/'+CFG.fileId+'/shares',{{method:'PUT',headers:{{'content-type':'application/json'}},body:JSON.stringify({{shares:state.shares}})}});
  dlg.close();post({{type:'drive:access-changed',fileId:CFG.fileId}});
}};
</script>"""
    return _layout(f.title, body, user, full=True)


ADMIN_CALLS: list[dict[str, Any]] = [
    {"label": "Hent budgetindstillinger", "method": "GET", "path": "/admin/v1/budget-settings"},
    {
        "label": "Gem budgetindstillinger",
        "method": "PUT",
        "path": "/admin/v1/budget-settings",
        "body": {
            "enabled": True,
            "nerfModel": "gpt-6-luna",
            "warningThresholds": [0.8, 0.95],
            "watcherEmails": [],
            "build": {
                "byRole": {
                    "laerer": {"nerfLimitTokens": 400000, "hardLimitTokens": 1000000},
                    "elev": {"nerfLimitTokens": None, "hardLimitTokens": None},
                    "admin": {"nerfLimitTokens": None, "hardLimitTokens": None},
                    "default": {"nerfLimitTokens": None, "hardLimitTokens": None},
                }
            },
            "app": {"perProject": {"nerfLimitTokens": 200000, "hardLimitTokens": 500000}},
        },
    },
    {"label": "Begrænsede brugere og apps", "method": "GET", "path": "/admin/v1/budgets/limited"},
    {
        "label": "Nulstil (bruger, build)",
        "method": "POST",
        "path": "/admin/v1/budgets/reset",
        "body": {"subject": "user", "id": "<sub>", "bucket": "build"},
    },
    {
        "label": "Giv ekstra (projekt, app)",
        "method": "POST",
        "path": "/admin/v1/budgets/grant",
        "body": {"subject": "project", "id": "<file id>", "bucket": "app", "extraTokens": 100000},
    },
    {"label": "Forbrug (sidste 7 dage)", "method": "GET", "path": "/admin/v1/usage"},
    {"label": "Egress-indstillinger", "method": "GET", "path": "/admin/v1/egress-settings"},
]


def admin_page(user: MiiraUser, subs: list[tuple[str, str, str]]) -> str:
    calls = json.dumps(ADMIN_CALLS)
    sub_rows = "".join(
        f"<li><span style='flex:1'>{escape(n)}</span><span class=tag>{escape(r)}</span><code>{escape(s)}</code></li>"
        for n, r, s in subs
    )
    body = f"""
<p class=muted>Kalder SlopTowns <code>/admin/v1/*</code> fra fake-miiras backend med en signeret admin-JWT.</p>
<div style="display:grid;grid-template-columns:320px 1fr;gap:16px">
 <div><ul class=list id=calls></ul></div>
 <div><p><select id=method><option>GET</option><option>PUT</option><option>POST</option></select>
   <input id=path style="width:70%"></p>
  <textarea id=body rows=10 style="width:100%;font-family:monospace"></textarea>
  <p><button class=primary id=send>Send</button> <span id=status class=muted></span></p><pre id=out></pre></div>
</div>
<h3>Brugere (SlopTown-sub)</h3><ul class=list>{sub_rows}</ul>
<script>
const CALLS={calls};
const ul=document.getElementById('calls');
CALLS.forEach(c=>{{const li=document.createElement('li');const b=document.createElement('button');b.textContent=c.label;b.style.width='100%';
 b.onclick=()=>{{document.getElementById('method').value=c.method;
  let p=c.path;if(c.method==='GET'&&p.endsWith('/usage')){{const to=new Date(),from=new Date(to-7*864e5);p+='?from='+from.toISOString()+'&to='+to.toISOString();}}
  document.getElementById('path').value=p;document.getElementById('body').value=c.body?JSON.stringify(c.body,null,2):'';}};
 li.append(b);ul.append(li);}});
document.getElementById('send').onclick=async()=>{{
  const method=document.getElementById('method').value,path=document.getElementById('path').value,raw=document.getElementById('body').value;
  let body=null;try{{body=raw.trim()?JSON.parse(raw):null}}catch(e){{document.getElementById('status').textContent='Ugyldig JSON';return;}}
  const r=await (await fetch('/admin/vibe/call',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{method,path,body}})}})).json();
  document.getElementById('status').textContent='HTTP '+r.status;document.getElementById('out').textContent=JSON.stringify(r.body,null,2);
}};
</script>"""
    return _layout("System admin · Vibe", body, user)

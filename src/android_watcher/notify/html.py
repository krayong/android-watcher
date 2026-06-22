"""Render a Digest into a standalone full-digest HTML page (all groups)."""

from __future__ import annotations

import html as _html

from android_watcher.models import Digest, DigestGroup
from android_watcher.rank import by_category

_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Android Watcher Digest</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
:root{--ink:#14171f;--paper:#faf9f5;--muted:#6b7180;--faint:#9aa0ac;--hair:#e7e6df;--signal:#1f9d57;--signal-soft:#e6f4ec;--card:#fff;--display:"Space Grotesk",system-ui,sans-serif;--body:"Inter",system-ui,sans-serif;--mono:"JetBrains Mono",ui-monospace,Menlo,monospace}
@media(prefers-color-scheme:dark){:root{--ink:#e9e8e2;--paper:#101218;--muted:#9aa0ac;--faint:#6b7180;--hair:#262932;--signal:#3ddc84;--signal-soft:#15271d;--card:#171a22}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:720px;margin:0 auto;padding:0 20px 72px}
header.mast{text-align:center;padding:48px 0 28px}
.mast h1{font-family:var(--display);font-weight:700;font-size:40px;letter-spacing:-.02em;margin:0;line-height:1.05}
.scan{width:64px;height:2px;margin:22px auto 18px;border-radius:2px;background:linear-gradient(90deg,transparent,var(--signal),transparent)}
.mast .stats{font-family:var(--mono);font-size:12.5px;color:var(--muted);display:flex;gap:14px;justify-content:center;flex-wrap:wrap}
.mast .stats b{color:var(--ink);font-weight:600}
.cat{display:flex;align-items:baseline;gap:12px;margin:38px 0 10px}
.cat h2{font-family:var(--mono);font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);font-weight:500;margin:0;white-space:nowrap}
.cat .rule{flex:1;height:1px;background:var(--hair)}.cat .n{font-family:var(--mono);font-size:12px;color:var(--faint)}
.row{background:var(--card);border:1px solid var(--hair);border-radius:12px;margin:8px 0;padding:16px 18px}
.titleline{display:flex;align-items:center;gap:10px}.tick{width:7px;height:7px;border-radius:50%;background:var(--signal);flex:none}
.title{font-family:var(--display);font-weight:600;font-size:17px;letter-spacing:-.01em}
.src{font-family:var(--mono);font-size:11px;color:var(--faint);margin:7px 0 0 17px}
.point{margin:9px 0 0 17px;color:var(--ink)}
.lbl{font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);margin:13px 0 6px 17px}
ol.sources{margin:0 0 0 17px;padding-left:22px}ol.sources li{margin:4px 0}
.src-one{margin:11px 0 0 17px;font-size:14px}
a{color:var(--signal)}a:hover{text-decoration:underline}.sources a,.src-one a{text-decoration:underline}
.banner{margin:0 0 24px;padding:12px 18px;border:1px solid #e6a817;border-radius:10px;background:#fffbee;color:#7a4f00;font-family:var(--mono);font-size:12.5px;text-align:center}
@media(prefers-color-scheme:dark){.banner{border-color:#7a4f00;background:#1e1600;color:#f5c842}}
.coverage{margin-top:44px;padding:24px;border:1px solid var(--hair);border-radius:14px;background:var(--card);display:flex;text-align:center}
.coverage .cell{flex:1;padding:4px 8px}.coverage .cell+.cell{border-left:1px solid var(--hair)}
.coverage .num{font-family:var(--display);font-weight:700;font-size:34px;letter-spacing:-.02em;color:var(--signal);line-height:1}
.coverage .lbl{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-top:8px}
footer{text-align:center;margin-top:22px;font-family:var(--mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
</style></head><body><div class="wrap">"""

_FOOT = "</div></body></html>"


def _esc(s: str) -> str:
	return _html.escape(s or "")


def clean_label(title: str) -> str:
	"""Display form of a page title: drop a trailing ' | Site' / ' — Site' suffix
	so link text reads as the page name, not 'Page | Android Open Source Project'."""
	if not title:
		return ""
	for sep in (" | ", " — ", " – ", " · "):
		if sep in title:
			return title.split(sep)[0].strip()
	return title.strip()


def _link(m) -> str:
	label = clean_label(m.title) or m.url
	return f'<a href="{_esc(m.url)}">{_esc(label)}</a>'


def _row(g: DigestGroup) -> str:
	parts = [
		'<div class="row">',
		f'<div class="titleline"><span class="tick"></span>'
		f'<span class="title">{_esc(clean_label(g.title))}</span></div>',
		f'<div class="src">{_esc(g.source_id)} · {_esc(g.change_kind)}</div>',
	]
	if g.summary:
		parts.append(f'<p class="point">{_esc(g.summary)}</p>')
	if g.page_count > 1:
		items = "".join(f"<li>{_link(m)}</li>" for m in g.members)
		parts.append(f'<div class="lbl">Sources</div><ol class="sources" type="i">{items}</ol>')
	else:
		parts.append(f'<div class="src-one">Source: {_link(g.members[0])}</div>')
	parts.append("</div>")
	return "".join(parts)


def render_html(digest: Digest) -> str:
	date = digest.generated_at.strftime("%d %b %Y")
	parts = [_HEAD]
	parts.append(
		'<header class="mast"><h1>Android Watcher Digest</h1><div class="scan"></div>'
		f'<div class="stats"><span><b>{date}</b></span>'
		f"<span><b>{len(digest.groups)}</b> groups</span>"
		f"<span><b>{digest.change_count()}</b> changes</span></div></header>"
	)
	if digest.ai_unavailable:
		parts.append(f'<div class="banner">AI unavailable: {_esc(digest.ai_unavailable)}</div>')
	for _cid, label, groups in by_category(digest.groups):
		parts.append(
			f'<div class="cat"><h2>{_esc(label)}</h2><span class="rule"></span>'
			f'<span class="n">{len(groups)}</span></div>'
		)
		parts.extend(_row(g) for g in groups)
	parts.append(
		'<div class="coverage">'
		f'<div class="cell"><div class="num">{digest.sources_scanned}</div>'
		'<div class="lbl">sources scanned</div></div>'
		f'<div class="cell"><div class="num">{digest.pages_watched:,}</div>'
		'<div class="lbl">pages watched</div></div></div>'
		"<footer>Android Watcher Digest</footer>"
	)
	parts.append(_FOOT)
	return "".join(parts)

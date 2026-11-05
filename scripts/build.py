#!/usr/bin/env python3
"""Build the animated SVG artwork used by README.md.

    python scripts/build.py            rebuild every SVG (activity card reads scripts/stats.json)
    python scripts/build.py --stats    refresh scripts/stats.json from the GitHub API first
                                       (token from GH_TOKEN or GITHUB_TOKEN)

Everything is plain SVG + CSS animation so it survives GitHub's <img> sandbox.
Text is set in Archivo (SIL Open Font License 1.1, see scripts/fonts/OFL.txt),
subset per file and embedded as a data URI.

Output:
    assets/hero.svg                     one dark plate, used in both GitHub themes
    assets/{dark,light}/*.svg           theme-matched section art
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import math
import os
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "scripts" / "fonts" / "Archivo[wdth,wght].ttf"
OUT = ROOT / "assets"
STATS = ROOT / "scripts" / "stats.json"
USER = "arnabxero"

# Colour tokens. Hues are shared across the README: pink is injected light / optical security,
# cyan autonomous vehicles, green bio-hybrid robotics, amber manufacturing, violet software & contests.
THEMES = {
    "dark": dict(
        fg="#E6EDF3", muted="#9198A1", faint="#6E7681", rule="#30363D", surface="#151B23",
        pink="#F85D7F", cyan="#56C8F0", green="#5EE6A8", amber="#F5CF5B", violet="#A58BFF",
        on_accent="#0D1117",
    ),
    "light": dict(
        fg="#1F2328", muted="#59636E", faint="#818B98", rule="#D1D9E0", surface="#F6F8FA",
        pink="#D7335F", cyan="#0A7EB8", green="#0C8A5A", amber="#A86A00", violet="#6A4FD6",
        on_accent="#FFFFFF",
    ),
}


# --------------------------------------------------------------------------- type & svg plumbing

def num(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@lru_cache(maxsize=None)
def _metrics(wdth: float, wght: float):
    font = instancer.instantiateVariableFont(TTFont(FONT), {"wdth": wdth, "wght": wght})
    return font.getBestCmap(), font["hmtx"].metrics, font["head"].unitsPerEm


class Style:
    def __init__(self, cls: str, size: float, wdth: float = 100, wght: float = 400, ls: float = 0.0):
        self.cls, self.size, self.wdth, self.wght, self.ls = cls, size, wdth, wght, ls

    def width(self, s: str) -> float:
        cmap, hmtx, upm = _metrics(self.wdth, self.wght)
        units = sum(hmtx[cmap[ord(c)]][0] if ord(c) in cmap else upm // 2 for c in s)
        return units * self.size / upm + self.ls * len(s)

    def wrap(self, s: str, width: float) -> list[str]:
        lines, cur = [], ""
        for word in s.split():
            trial = f"{cur} {word}".strip()
            if cur and self.width(trial) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        return lines + ([cur] if cur else [])

    def css(self) -> str:
        return (f".{self.cls}{{font-size:{num(self.size)}px;font-stretch:{num(self.wdth)}%;"
                f"font-weight:{num(self.wght)};letter-spacing:{num(self.ls)}px}}")


@lru_cache(maxsize=None)
def font_data(chars: str) -> str:
    opts = subset.Options()
    opts.flavor = "woff"
    opts.hinting = False
    opts.layout_features = ["kern", "liga", "calt", "tnum", "lnum", "case", "ccmp", "locl", "mark", "mkmk"]
    font = TTFont(FONT, recalcTimestamp=False)
    sub = subset.Subsetter(opts)
    sub.populate(text=chars + " ")
    sub.subset(font)
    buf = io.BytesIO()
    font.flavor = "woff"
    font.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


class Doc:
    """Collects markup, styles and the characters used, then emits a self-contained SVG."""

    def __init__(self, w: int, h: int, title: str, desc: str):
        self.w, self.h, self.title, self.desc = w, h, title, desc
        self.chars: set[str] = set()
        self.styles: dict[str, Style] = {}
        self.css: list[str] = []
        self.defs: list[str] = []
        self.body: list[str] = []

    def text(self, x, y, s, st: Style, fill=None, anchor=None, cls="", extra="") -> str:
        self.chars.update(s)
        self.styles[st.cls] = st
        attrs = f'x="{num(x)}" y="{num(y)}" class="{(st.cls + " " + cls).strip()}"'
        if fill:
            attrs += f' fill="{fill}"'
        if anchor:
            attrs += f' text-anchor="{anchor}"'
        return f"<text {attrs}{extra}>{escape(s)}</text>"

    def use_text(self, s: str, st: Style):
        self.chars.update(s)
        self.styles[st.cls] = st

    def add(self, *parts: str):
        self.body.extend(parts)

    def render(self) -> str:
        face = (f"@font-face{{font-family:A;src:url(data:font/woff;base64,{font_data(''.join(sorted(self.chars)))}) "
                f"format('woff');font-weight:100 900;font-stretch:62% 125%}}")
        base = "text{font-family:A,'Segoe UI',Helvetica,Arial,sans-serif;font-kerning:normal}"
        calm = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        css = "".join([face, base] + [s.css() for s in self.styles.values()] + self.css + [calm])
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" role="img" aria-labelledby="t d">'
            f'<title id="t">{escape(self.title)}</title><desc id="d">{escape(self.desc)}</desc>'
            f"<style>{css}</style><defs>{''.join(self.defs)}</defs>{''.join(self.body)}</svg>"
        )


def polar(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def arc(cx, cy, r, a0, a1):
    """Clockwise arc (screen coordinates) from angle a0 to a1 in degrees."""
    x0, y0 = polar(cx, cy, r, a0)
    x1, y1 = polar(cx, cy, r, a1)
    large = 1 if (a1 - a0) % 360 > 180 else 0
    return f"M{num(x0)} {num(y0)}A{num(r)} {num(r)} 0 {large} 1 {num(x1)} {num(y1)}"


def ngon(cx, cy, r, n, rot=-90.0):
    return " ".join(f"{num(x)},{num(y)}" for x, y in (polar(cx, cy, r, rot + i * 360 / n) for i in range(n)))


def circle_path(cx, cy, r, clockwise=True):
    s = 1 if clockwise else 0
    return (f"M{num(cx - r)} {num(cy)}A{num(r)} {num(r)} 0 1 {s} {num(cx + r)} {num(cy)}"
            f"A{num(r)} {num(r)} 0 1 {s} {num(cx - r)} {num(cy)}")


def rect_path(x, y, w, h, r=0.0):
    if not r:
        return f"M{num(x)} {num(y)}H{num(x + w)}V{num(y + h)}H{num(x)}Z"
    return (f"M{num(x + r)} {num(y)}H{num(x + w - r)}A{num(r)} {num(r)} 0 0 1 {num(x + w)} {num(y + r)}"
            f"V{num(y + h - r)}A{num(r)} {num(r)} 0 0 1 {num(x + w - r)} {num(y + h)}H{num(x + r)}"
            f"A{num(r)} {num(r)} 0 0 1 {num(x)} {num(y + h - r)}V{num(y + r)}A{num(r)} {num(r)} 0 0 1 {num(x + r)} {num(y)}Z")


def brackets(x, y, w, h, n=18):
    """Viewfinder corner marks."""
    return (f"M{x} {y + n}V{y}H{x + n}M{x + w - n} {y}H{x + w}V{y + n}"
            f"M{x + w} {y + h - n}V{y + h}H{x + w - n}M{x + n} {y + h}H{x}V{y + h - n}")


def write(path: Path, svg: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8", newline="\n")
    print(f"  {path.relative_to(ROOT)}  {len(svg) / 1024:.0f} KB")


# --------------------------------------------------------------------------- hero

def build_hero():
    W, H = 1000, 400
    cx, cy = 212, 200
    P = dict(fg="#EEF2F8", muted="#A3ADC2", faint="#647089", pink="#F85D7F", cyan="#56C8F0",
             green="#5EE6A8", amber="#F5CF5B", violet="#A58BFF")
    d = Doc(W, H, "Eftakhar Ahmed Arnob",
            "A camera lens with an opening iris catches an out-of-view laser and throws a lens-flare streak "
            "beneath the name Eftakhar Ahmed Arnob, Ph.D. researcher at Auburn University.")

    x0, right = 446, 962
    eyebrow = Style("ey", 16.5, 100, 520, 0.1)
    name_probe = Style("nm", 100, 125, 800, -1)
    size = min(54.0, 100 * (right - x0) / name_probe.width("Eftakhar Ahmed"))
    name = Style("nm", round(size, 1), 125, 800, -round(size / 90, 2))
    alias = Style("al", 14.5, 100, 450, 0.15)
    role = Style("ro", 21, 100, 560, 0)
    lab = Style("lb", 14, 100, 520, 0.2)
    engr = Style("en", 10.5, 125, 640, 2.1)

    roles = [
        "Securing smart cameras against light injection",
        "Testing self-driving perception on real hardware",
        "Building robots that feel through living neurons",
        "Co-inventor on 3 patents, 3× ICPC regionalist",
    ]
    role_st = role
    widest = max(role.width(r) for r in roles)
    if widest > right - x0:
        role_st = Style("ro", round(21 * (right - x0) / widest, 1), 100, 560, 0)

    d.defs.append(f"""
<clipPath id="plate"><rect width="{W}" height="{H}" rx="22"/></clipPath>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#101A36"/><stop offset=".5" stop-color="#080D1A"/><stop offset="1" stop-color="#04060C"/></linearGradient>
<radialGradient id="halo" cx="{cx}" cy="{cy}" r="420" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{P['pink']}" stop-opacity=".2"/><stop offset=".35" stop-color="#6D5BFF" stop-opacity=".07"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
<radialGradient id="vig" cx="500" cy="200" r="640" gradientUnits="userSpaceOnUse"><stop offset=".55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".55"/></radialGradient>
<pattern id="bayer" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="4" height="4" fill="#FF4D6D"/><rect x="4" width="4" height="4" fill="#4DFFA6"/><rect y="4" width="4" height="4" fill="#4DFFA6"/><rect x="4" y="4" width="4" height="4" fill="#4D8DFF"/></pattern>
<linearGradient id="fadeR" x1="0" x2="1"><stop offset=".25" stop-color="#fff" stop-opacity="0"/><stop offset=".6" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity=".5"/></linearGradient>
<mask id="sensorMask"><rect width="{W}" height="{H}" fill="url(#fadeR)"/></mask>
<linearGradient id="scanG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".85" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<mask id="scanMask"><rect class="scan" x="0" y="-70" width="{W}" height="70" fill="url(#scanG)"/></mask>
<radialGradient id="metal" cx="{cx - 60}" cy="{cy - 80}" r="260" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#2A3350"/><stop offset=".6" stop-color="#141A2B"/><stop offset="1" stop-color="#090C15"/></radialGradient>
<radialGradient id="glass" cx="{cx - 30}" cy="{cy - 40}" r="150" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#1D2C55"/><stop offset=".55" stop-color="#0A1129"/><stop offset="1" stop-color="#03050B"/></radialGradient>
<linearGradient id="blade" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1A2134"/><stop offset="1" stop-color="#0B0F1A"/></linearGradient>
<radialGradient id="bloomG"><stop offset="0" stop-color="#fff" stop-opacity=".95"/><stop offset=".22" stop-color="{P['pink']}" stop-opacity=".75"/><stop offset=".6" stop-color="{P['violet']}" stop-opacity=".18"/><stop offset="1" stop-color="{P['violet']}" stop-opacity="0"/></radialGradient>
<linearGradient id="beamG" x1="-40" y1="-34" x2="{cx}" y2="{cy}" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{P['pink']}" stop-opacity="0"/><stop offset=".55" stop-color="{P['pink']}" stop-opacity=".55"/><stop offset="1" stop-color="#fff"/></linearGradient>
<linearGradient id="streakG" x1="0" x2="{W}" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{P['pink']}" stop-opacity="0"/><stop offset=".1" stop-color="{P['pink']}" stop-opacity=".45"/><stop offset="{num(cx / W)}" stop-color="#fff"/><stop offset=".42" stop-color="{P['violet']}" stop-opacity=".55"/><stop offset=".78" stop-color="{P['cyan']}" stop-opacity=".2"/><stop offset="1" stop-color="{P['cyan']}" stop-opacity="0"/></linearGradient>
<filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3"/></filter>
<filter id="wide" x="-5%" y="-400%" width="110%" height="900%"><feGaussianBlur stdDeviation="4"/></filter>
<filter id="haze" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="1.6"/></filter>
<clipPath id="glassClip"><circle cx="{cx}" cy="{cy}" r="126"/></clipPath>
<clipPath id="apClip"><circle cx="{cx}" cy="{cy}" r="108"/></clipPath>
<mask id="irisMask"><circle cx="{cx}" cy="{cy}" r="108" fill="#fff"/><g class="iris"><polygon points="{ngon(cx, cy, 100, 9)}" fill="#000"/></g></mask>
<path id="ringTop" d="{circle_path(cx, cy, 145, True)}"/>
<path id="ringBot" d="{circle_path(cx, cy, 152.5, False)}"/>
""")

    css = """
.scan{animation:scan 7s linear infinite}
@keyframes scan{from{transform:translateY(0)}to{transform:translateY(480px)}}
.bands{animation:bands 1.1s linear infinite}
@keyframes bands{from{transform:translateY(0)}to{transform:translateY(22px)}}
.iris{transform-origin:CXpx CYpx;transform:scale(.62);animation:open 1.5s cubic-bezier(.2,.7,.2,1) both,react 8s 1.5s infinite}
@keyframes open{from{transform:rotate(50deg) scale(.1)}to{transform:rotate(0) scale(.62)}}
@keyframes react{0%,56%{transform:rotate(0) scale(.62)}61%{transform:rotate(14deg) scale(.36)}80%,100%{transform:rotate(0) scale(.62)}}
.knurl{transform-origin:CXpx CYpx;animation:turn 1.9s cubic-bezier(.2,.7,.2,1) both}
@keyframes turn{from{transform:rotate(-16deg)}to{transform:rotate(0)}}
.beam{opacity:.3;animation:beam 8s 1.5s infinite}
@keyframes beam{0%,54%{opacity:.3}59%{opacity:1}70%{opacity:.6}86%,100%{opacity:.3}}
.streak{transform-origin:CXpx CYpx;opacity:.5;animation:streak 8s 1.5s infinite}
@keyframes streak{0%,54%{opacity:.5;transform:scaleX(1)}59%{opacity:1;transform:scaleX(1.12)}76%{opacity:.7}92%,100%{opacity:.5;transform:scaleX(1)}}
.bloom{transform-origin:CXpx CYpx;opacity:.75;animation:bloom 8s 1.5s infinite}
@keyframes bloom{0%,54%{opacity:.75;transform:scale(1)}59%{opacity:1;transform:scale(1.7)}80%,100%{opacity:.75;transform:scale(1)}}
.gh{opacity:.4;animation:gh 8s 1.5s infinite}
@keyframes gh{0%,55%{opacity:.4}61%{opacity:1}82%,100%{opacity:.4}}
.nm{animation:focus 1.7s .1s cubic-bezier(.2,.7,.2,1) both}
@keyframes focus{from{opacity:0;filter:blur(12px)}to{opacity:1;filter:blur(0)}}
.late{animation:fade 1.2s .9s both}
@keyframes fade{from{opacity:0}to{opacity:1}}
.rl{opacity:0;animation:rl 16s infinite both}
.r0{opacity:1;animation-delay:.6s}.r1{animation-delay:4.6s}.r2{animation-delay:8.6s}.r3{animation-delay:12.6s}
@keyframes rl{0%{opacity:0;filter:blur(8px)}4%{opacity:1;filter:blur(0)}22%{opacity:1;filter:blur(0)}25%{opacity:0;filter:blur(8px)}100%{opacity:0}}
""".replace("CX", str(cx)).replace("CY", str(cy))
    d.css.append(css)

    # plate, glow, sensor texture with rolling readout
    knurl = "".join(
        f'<line x1="{num(polar(cx, cy, 165, a)[0])}" y1="{num(polar(cx, cy, 165, a)[1])}" '
        f'x2="{num(polar(cx, cy, 178, a)[0])}" y2="{num(polar(cx, cy, 178, a)[1])}"/>'
        for a in [i * 2.5 for i in range(144)]
    )
    bands = "".join(
        f'<rect x="{cx - 120}" y="{cy - 140 + i * 22}" width="240" height="9" fill="{P["pink"] if i % 2 else P["violet"]}" '
        f'opacity="{".26" if i % 2 else ".16"}"/>' for i in range(14)
    )
    blade_lines = []
    verts = [polar(cx, cy, 100, -90 + i * 40) for i in range(9)]
    for i in range(9):
        (ax, ay), (bx, by) = verts[i], verts[(i + 1) % 9]
        ux, uy = bx - ax, by - ay
        k = 260 / math.hypot(ux, uy)
        blade_lines.append(f'<line x1="{num(bx)}" y1="{num(by)}" x2="{num(bx + ux * k)}" y2="{num(by + uy * k)}"/>')
    dof = [P["pink"], P["cyan"], P["green"], P["amber"], P["violet"]]
    dof_ticks = "".join(
        f'<line x1="{num(polar(cx, cy, 156.5, a)[0])}" y1="{num(polar(cx, cy, 156.5, a)[1])}" '
        f'x2="{num(polar(cx, cy, 161.5, a)[0])}" y2="{num(polar(cx, cy, 161.5, a)[1])}" stroke="{c}"/>'
        for a, c in zip([-98, -94, -90, -86, -82], dof)
    )
    top_txt = "EFTAKHAR AHMED ARNOB  ·  AUBURN UNIVERSITY"
    bot_txt = "CYBER-PHYSICAL SECURITY  ·  1:1.4  f = 50 mm"
    d.use_text(top_txt + bot_txt, engr)

    ux, uy = cx + 40, cy + 34  # direction from the source (-40,-34) through the lens centre
    ln = math.hypot(ux, uy)
    ux, uy = ux / ln, uy / ln
    ghosts = [(206, 12, P["green"]), (292, 26, P["violet"]), (-62, 9, P["amber"])]
    ghost_svg = "".join(
        f'<polygon points="{ngon(cx + ux * t, cy + uy * t, r, 9, -90 + t / 7)}" fill="{c}" fill-opacity=".1" '
        f'stroke="{c}" stroke-opacity=".38" stroke-width="1"/>' for t, r, c in ghosts
    )

    d.add(f'<g clip-path="url(#plate)">',
          f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
          f'<rect width="{W}" height="{H}" fill="url(#halo)"/>',
          f'<g mask="url(#sensorMask)"><rect width="{W}" height="{H}" fill="url(#bayer)" opacity=".035"/>'
          f'<rect width="{W}" height="{H}" fill="url(#bayer)" opacity=".2" mask="url(#scanMask)"/></g>',
          # lens barrel
          f'<circle cx="{cx}" cy="{cy}" r="179" fill="url(#metal)" stroke="#2B3450"/>',
          f'<g class="knurl" stroke="#0A0E18" stroke-width="1.6">{knurl}</g>',
          f'<circle cx="{cx}" cy="{cy}" r="164.5" fill="#0D1220" stroke="#2B3450" stroke-width="1"/>',
          f'<circle cx="{cx}" cy="{cy}" r="133" fill="#070A13" stroke="#232B42"/>',
          f'<text class="en" fill="#C9D1E3" fill-opacity=".72"><textPath href="#ringTop" xlink:href="#ringTop" startOffset="25%" text-anchor="middle">{escape(top_txt)}</textPath></text>',
          f'<text class="en" fill="#C9D1E3" fill-opacity=".55"><textPath href="#ringBot" xlink:href="#ringBot" startOffset="25%" text-anchor="middle">{escape(bot_txt)}</textPath></text>',
          f'<g stroke-width="2.4" stroke-linecap="round">{dof_ticks}</g>',
          f'<circle cx="{cx}" cy="{cy}" r="126" fill="url(#glass)"/>',
          # what the sensor sees through the aperture: rolling-shutter bands from the injected light
          f'<g clip-path="url(#apClip)"><rect x="{cx - 110}" y="{cy - 110}" width="220" height="220" fill="#04050A"/>'
          f'<g class="bands">{bands}</g>'
          f'<circle class="bloom" cx="{cx}" cy="{cy}" r="52" fill="url(#bloomG)"/></g>',
          # iris blades
          f'<rect x="{cx - 110}" y="{cy - 110}" width="220" height="220" fill="url(#blade)" mask="url(#irisMask)"/>',
          f'<g clip-path="url(#apClip)"><g class="iris" stroke="#2E3854" stroke-width="1.1" vector-effect="non-scaling-stroke">'
          + "".join(bl.replace("/>", ' vector-effect="non-scaling-stroke"/>') for bl in blade_lines) + "</g></g>",
          f'<circle cx="{cx}" cy="{cy}" r="108" fill="none" stroke="#000" stroke-opacity=".55" stroke-width="4"/>',
          # multicoating reflections on the front element
          f'<g clip-path="url(#glassClip)" fill="none" stroke-linecap="round" filter="url(#haze)">'
          f'<path d="{arc(cx, cy, 114, 196, 248)}" stroke="{P["green"]}" stroke-opacity=".42" stroke-width="5"/>'
          f'<path d="{arc(cx, cy, 98, 204, 232)}" stroke="{P["violet"]}" stroke-opacity=".45" stroke-width="3.5"/>'
          f'<path d="{arc(cx, cy, 119, 18, 58)}" stroke="{P["pink"]}" stroke-opacity=".35" stroke-width="3"/>'
          f'<ellipse cx="{cx - 46}" cy="{cy - 66}" rx="9" ry="3.5" transform="rotate(-38 {cx - 46} {cy - 66})" fill="{P["amber"]}" fill-opacity=".55" stroke="none"/>'
          f'</g>',
          f'<circle cx="{cx}" cy="{cy}" r="126" fill="none" stroke="#3A4566" stroke-width="1.2"/>',
          # the out-of-view laser, its flare streak and aperture-shaped ghosts
          f'<g class="beam"><line x1="-40" y1="-34" x2="{cx}" y2="{cy}" stroke="url(#beamG)" stroke-width="9" opacity=".35" filter="url(#soft)"/>'
          f'<line x1="-40" y1="-34" x2="{cx}" y2="{cy}" stroke="url(#beamG)" stroke-width="1.6"/></g>',
          f'<g class="streak"><rect x="0" y="{cy - 5}" width="{W}" height="10" fill="url(#streakG)" opacity=".45" filter="url(#wide)"/>'
          f'<rect x="0" y="{cy - .6}" width="{W}" height="1.2" fill="url(#streakG)"/></g>',
          f'<g class="gh">{ghost_svg}</g>',
          f'<rect width="{W}" height="{H}" fill="url(#vig)"/>',
          "</g>",
          f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="21.5" fill="none" stroke="#fff" stroke-opacity=".08"/>')

    # type
    d.add(d.text(x0, 96, "Ph.D. researcher, Auburn University", eyebrow, P["muted"], cls="late"))
    d.add(f'<g class="nm">'
          + d.text(x0 - 3, 152, "Eftakhar Ahmed", name, P["fg"])
          + d.text(x0 - 3, 152 + name.size * 0.98, "Arnob", name, P["fg"]) + "</g>")
    d.add(d.text(x0, 236, "also written Iftekhar Ahmed Arnab", alias, P["faint"], cls="late"))
    for i, r in enumerate(roles):
        d.add(d.text(x0, 286, r, role_st, P["fg"], cls=f"rl r{i}"))
    labs = [("Cyber-Physical Security Lab", P["pink"]), ("NiFELab", P["green"]), ("ICAMS Lab", P["amber"])]
    x, parts = x0, []
    for label, c in labs:
        parts.append(f'<circle cx="{num(x + 4)}" cy="339" r="4" fill="{c}"/>')
        parts.append(d.text(x + 15, 344, label, lab, P["muted"]))
        x += 15 + lab.width(label) + 26
    d.add(f'<g class="late">{"".join(parts)}</g>')
    write(OUT / "hero.svg", d.render())


# --------------------------------------------------------------------------- section headers

HEADERS = {
    "research": "Research",
    "patents": "Patents",
    "publications": "Publications & press",
    "journey": "Journey",
    "toolkit": "Toolkit",
    "talks": "Talks & service",
    "activity": "GitHub activity",
    "videos": "Latest videos",
}


def build_header(slug: str, title: str, theme: str):
    C = THEMES[theme]
    W, H = 1000, 64
    d = Doc(W, H, title, f"Section heading: {title}")
    st = Style("h", 27, 125, 760, -0.3)
    d.add(f'<polygon points="{ngon(14, 31, 9, 9)}" fill="none" stroke="{C["pink"]}" stroke-width="2.2" stroke-linejoin="round"/>')
    d.add(d.text(36, 41, title, st, C["fg"]))
    start = 36 + st.width(title) + 22
    d.defs.append(f'<linearGradient id="fade" x1="{num(start)}" x2="{W}" gradientUnits="userSpaceOnUse">'
                  f'<stop offset="0" stop-color="{C["faint"]}"/><stop offset="1" stop-color="{C["faint"]}" stop-opacity="0"/></linearGradient>')
    ticks = []
    x = start
    i = 0
    while x < W - 4:
        h = 9 if i % 5 == 0 else 4
        ticks.append(f"M{num(x)} {num(32 - h)}V32")
        x += 9
        i += 1
    d.add(f'<path d="M{num(start)} 32.5H{W}" stroke="url(#fade)" stroke-width="1"/>',
          f'<path d="{"".join(ticks)}" stroke="url(#fade)" stroke-width="1"/>')
    write(OUT / theme / f"h-{slug}.svg", d.render())


# --------------------------------------------------------------------------- research grid

RESEARCH = [
    dict(
        key="optics", hue="pink",
        title="Light-injection attacks on cameras",
        body="Found physical-layer flaws in nine smart cameras, including Google Nest, Amazon Ring and Arlo, "
             "and built training-free detection, sensorless feed recovery and a lens shield.",
        lab="Cyber-Physical Security Lab, advised by Dr. Yazhou Tu",
    ),
    dict(
        key="av", hue="cyan",
        title="Autonomous vehicle & CPS security",
        body="Hardware-in-the-loop adversarial testing of self-driving perception (detection, lanes, depth), "
             "plus SLAM/IMU fusion, GPS spoofing defense and BCI prosthetic security.",
        lab="Cyber-Physical Security Lab",
    ),
    dict(
        key="bio", hue="green",
        title="Bio-hybrid robotics",
        body="Robotic and prosthetic systems that make decisions with biological neurons and sense touch "
             "through hydrogel and living-neuron skin.",
        lab="NiFELab, Chemical Engineering, with Dr. Jean-François Louf",
    ),
    dict(
        key="mfg", hue="amber",
        title="Smart manufacturing & CCTV safety",
        body="Plant-management software, machine data collection and automation, and computer-vision "
             "CCTV monitoring that checks safety compliance on the floor.",
        lab="ICAMS Lab, Industrial Engineering, with Dr. John Osho",
    ),
]


def ill_optics(C, d: Doc, uid: str):
    k, m, s = C["pink"], C["muted"], C["surface"]
    small = Style("xs", 12.5, 100, 520, 0.2)
    scene = (f'<path d="M0 64H{{w}}" stroke="{m}" stroke-opacity=".5"/>'
             f'<circle cx="{{px}}" cy="34" r="7" fill="none" stroke="{m}" stroke-width="1.5"/>'
             f'<path d="M{{px}} 41V58M{{pl}} 64L{{px}} 58L{{pr}} 64M{{pl2}} 48H{{pr2}}" stroke="{m}" stroke-width="1.5" fill="none"/>')

    def frame(x, w, inner):
        px = w / 2
        sc = (scene.replace("{w}", num(w)).replace("{px}", num(px)).replace("{pl}", num(px - 7))
              .replace("{pr}", num(px + 7)).replace("{pl2}", num(px - 9)).replace("{pr2}", num(px + 9)))
        return (f'<g transform="translate({x} 14)"><rect width="{w}" height="84" rx="5" fill="{s}" stroke="{C["rule"]}"/>'
                f'<g clip-path="url(#f{uid}{x})">{sc}{inner}</g></g>')

    d.defs.append(f'<clipPath id="f{uid}168"><rect width="118" height="84" rx="5"/></clipPath>'
                  f'<clipPath id="f{uid}316"><rect width="118" height="84" rx="5"/></clipPath>')
    bands = "".join(f'<rect y="{-16 + i * 16}" width="118" height="7" fill="{k}" opacity=".38"/>' for i in range(8))
    return (
        # emitter + beam
        f'<rect x="6" y="88" width="26" height="14" rx="3" fill="none" stroke="{m}" stroke-width="1.5"/>'
        f'<circle cx="32" cy="95" r="2.5" fill="{k}"/>'
        f'<path class="flow" d="M34 94L96 46" stroke="{k}" stroke-width="2" stroke-dasharray="5 5" fill="none"/>'
        # dome camera
        f'<path d="M70 14H136M76 14V20H130V14M78 20A25 25 0 0 0 128 20" fill="none" stroke="{C["fg"]}" stroke-width="1.6"/>'
        f'<circle cx="103" cy="32" r="8" fill="{s}" stroke="{C["fg"]}" stroke-width="1.6"/><circle cx="103" cy="32" r="3" fill="{k}"/>'
        f'<path d="M140 56H160" stroke="{m}" stroke-width="1.5"/><path d="M156 52L161 56L156 60" stroke="{m}" stroke-width="1.5" fill="none"/>'
        + frame(168, 118, f'<g class="roll">{bands}</g>')
        + f'<path d="M292 56H310" stroke="{m}" stroke-width="1.5"/><path d="M306 52L311 56L306 60" stroke="{m}" stroke-width="1.5" fill="none"/>'
        + frame(316, 118, f'<rect class="sweep" y="0" width="118" height="2" fill="{k}" opacity=".9"/>')
        + d.text(227, 116, "injected", small, m, anchor="middle")
        + d.text(375, 116, "restored", small, m, anchor="middle")
    )


def ill_av(C, d: Doc, uid: str):
    k, m = C["cyan"], C["muted"]
    tag = Style("tg", 11.5, 100, 640, 0.2)
    hil = Style("hl", 11, 112, 700, 0.6)
    pins = "".join(f"M{386 + i * 8} 22V27M{386 + i * 8} 55V60" for i in range(4))
    return (
        f'<path d="M40 120L206 6M396 120L230 6" stroke="{m}" stroke-width="1.5" fill="none"/>'
        f'<path d="M150 6H286" stroke="{m}" stroke-opacity=".35"/>'
        f'<path class="lane" d="M218 120V8" stroke="{m}" stroke-width="2" stroke-dasharray="12 10" fill="none"/>'
        # car, rear view
        f'<rect x="184" y="70" width="72" height="30" rx="6" fill="{C["surface"]}" stroke="{C["fg"]}" stroke-width="1.6"/>'
        f'<path d="M193 70L199 56H241L247 70" fill="none" stroke="{C["fg"]}" stroke-width="1.6"/>'
        f'<rect x="189" y="78" width="12" height="5" rx="1.5" fill="{C["pink"]}"/><rect x="239" y="78" width="12" height="5" rx="1.5" fill="{C["pink"]}"/>'
        f'<rect x="188" y="100" width="10" height="7" rx="2" fill="{C["fg"]}"/><rect x="242" y="100" width="10" height="7" rx="2" fill="{C["fg"]}"/>'
        # detection box: honest, then fooled
        f'<g class="ok"><rect x="176" y="48" width="88" height="64" fill="none" stroke="{k}" stroke-width="1.8"/>'
        f'<rect x="176" y="34" width="62" height="15" fill="{k}"/>{d.text(181, 45, "car 0.97", tag, C["on_accent"])}</g>'
        f'<g class="bad"><rect x="208" y="30" width="86" height="40" fill="none" stroke="{C["pink"]}" stroke-width="1.8" stroke-dasharray="4 3"/>'
        f'<rect x="208" y="16" width="68" height="15" fill="{C["pink"]}"/>{d.text(213, 27, "sign 0.41", tag, C["on_accent"])}</g>'
        # hardware in the loop
        f'<rect x="378" y="27" width="40" height="28" rx="3" fill="{C["surface"]}" stroke="{C["fg"]}" stroke-width="1.6"/>'
        f'<path d="{pins}" stroke="{C["fg"]}" stroke-width="1.6"/>'
        + d.text(398, 45.5, "HIL", hil, k, anchor="middle")
        + f'<path class="flow" d="M376 41C330 41 300 60 266 76" stroke="{k}" stroke-width="1.5" stroke-dasharray="4 4" fill="none"/>'
    )


def ill_bio(C, d: Doc, uid: str):
    k, fg = C["green"], C["fg"]
    chambers = ""
    for i in range(7):
        x = 26 + i * 27
        drop = (i ** 1.8) * 0.9
        rot = i * 3.2
        chambers += (f'<rect x="{x}" y="{num(34 + drop)}" width="22" height="34" rx="7" fill="{C["surface"]}" '
                     f'stroke="{fg}" stroke-width="1.5" transform="rotate({num(rot)} {x + 11} {num(51 + drop)})"/>')
    paths = [
        "M246 92C268 70 280 44 312 36",
        "M246 92C284 92 300 70 336 66",
        "M246 92C270 104 306 106 330 96",
        "M312 36C338 30 360 30 384 40",
        "M336 66C356 62 372 54 384 40",
        "M330 96C356 100 380 88 404 74",
    ]
    nerves = "".join(f'<path d="{p}" stroke="{k}" stroke-opacity=".35" stroke-width="1.4" fill="none"/>' for p in paths)
    pulses = "".join(
        f'<path class="pulse p{i}" d="{p}" pathLength="100" stroke="{k}" stroke-width="2.6" stroke-linecap="round" '
        f'stroke-dasharray="8 92" fill="none"/>' for i, p in enumerate(paths)
    )
    somas = "".join(f'<circle class="soma s{i}" cx="{x}" cy="{y}" r="{r}" fill="{k}" fill-opacity=".22" stroke="{k}" stroke-width="1.5"/>'
                    for i, (x, y, r) in enumerate([(312, 36, 5), (336, 66, 5), (330, 96, 5), (384, 40, 8), (404, 74, 6.5)]))
    return (
        f'<rect x="4" y="30" width="22" height="46" rx="3" fill="none" stroke="{fg}" stroke-width="1.5"/>'
        f'<path d="M26 72C90 76 170 86 236 104" stroke="{fg}" stroke-width="1.5" fill="none"/>'
        + chambers
        + f'<circle cx="246" cy="106" r="15" fill="none" stroke="{C["muted"]}" stroke-width="1.5"/>'
        f'<circle class="ripple" cx="246" cy="92" r="6" fill="none" stroke="{k}" stroke-width="1.6"/>'
        f'<circle class="ripple q" cx="246" cy="92" r="6" fill="none" stroke="{k}" stroke-width="1.6"/>'
        + nerves + pulses + somas
    )


def ill_mfg(C, d: Doc, uid: str):
    k, fg, m = C["amber"], C["fg"], C["muted"]
    tag = Style("tg", 11.5, 100, 640, 0.2)
    rollers = "".join(f'<circle cx="{34 + i * 30}" cy="100" r="5" fill="none" stroke="{m}" stroke-width="1.4"/>' for i in range(10))
    boxes = "".join(f'<rect x="{-62 + i * 80}" y="70" width="30" height="24" rx="2" fill="{C["surface"]}" stroke="{fg}" stroke-width="1.5"/>'
                    f'<path d="M{-62 + i * 80} 79H{-32 + i * 80}" stroke="{fg}" stroke-width="1"/>' for i in range(6))
    d.defs.append(f'<clipPath id="belt{uid}"><rect x="22" y="40" width="300" height="60"/></clipPath>')
    return (
        f'<rect x="22" y="94" width="300" height="12" rx="6" fill="none" stroke="{fg}" stroke-width="1.5"/>' + rollers
        + f'<g clip-path="url(#belt{uid})"><g class="belt">{boxes}</g></g>'
        # CCTV camera with its field of view
        f'<path d="M418 4V16H398" stroke="{fg}" stroke-width="1.6" fill="none"/>'
        f'<g transform="rotate(-24 372 22)"><rect x="350" y="14" width="46" height="16" rx="3" fill="{C["surface"]}" stroke="{fg}" stroke-width="1.6"/>'
        f'<rect x="344" y="16" width="6" height="12" rx="1" fill="{fg}"/></g>'
        f'<path d="M347 34L214 66L300 104Z" fill="{k}" fill-opacity=".1" stroke="{k}" stroke-opacity=".5" stroke-dasharray="3 3"/>'
        f'<rect class="blink" x="236" y="64" width="44" height="34" fill="none" stroke="{k}" stroke-width="1.8"/>'
        # worker with hard hat, checked for PPE
        f'<circle cx="398" cy="66" r="7" fill="none" stroke="{fg}" stroke-width="1.6"/>'
        f'<path d="M390 63A8 8 0 0 1 406 63Z" fill="{k}"/>'
        f'<path d="M398 73V96M398 96L390 114M398 96L406 114M386 82H410" stroke="{fg}" stroke-width="1.6" fill="none"/>'
        f'<rect x="378" y="50" width="40" height="68" fill="none" stroke="{k}" stroke-width="1.5" stroke-dasharray="4 3"/>'
        f'<rect x="378" y="36" width="50" height="15" fill="{k}"/>' + d.text(383, 47, "helmet", tag, C["on_accent"])
    )


ILLUSTRATIONS = {"optics": ill_optics, "av": ill_av, "bio": ill_bio, "mfg": ill_mfg}


def build_research(theme: str):
    C = THEMES[theme]
    W, H = 1000, 650
    fw, fh, gap = 480, 305, 40
    d = Doc(W, H, "Research areas",
            "Four research areas: light-injection attacks on cameras; autonomous vehicle and cyber-physical "
            "security; bio-hybrid robotics; smart manufacturing and CCTV safety monitoring.")
    title = Style("ti", 21, 112, 720, -0.1)
    body = Style("bd", 15.5, 100, 420, 0.05)
    lab = Style("lb", 13.5, 100, 560, 0.15)
    d.css.append("""
.flow{animation:flow 1s linear infinite}@keyframes flow{to{stroke-dashoffset:-10}}
.roll{animation:roll 1.2s linear infinite}@keyframes roll{to{transform:translateY(16px)}}
.sweep{animation:sweep 2.6s cubic-bezier(.5,0,.5,1) infinite}@keyframes sweep{0%{transform:translateY(0);opacity:0}10%{opacity:.9}90%{opacity:.9}100%{transform:translateY(82px);opacity:0}}
.lane{animation:lane .8s linear infinite}@keyframes lane{to{stroke-dashoffset:-22}}
.ok{animation:ok 5s infinite}@keyframes ok{0%,60%{opacity:1}64%,84%{opacity:0}88%,100%{opacity:1}}
.bad{opacity:0;animation:bad 5s infinite}@keyframes bad{0%,61%{opacity:0}64%,84%{opacity:1}87%,100%{opacity:0}}
.pulse{stroke-dashoffset:100;animation:pulse 2.4s linear infinite}@keyframes pulse{0%{stroke-dashoffset:108}70%,100%{stroke-dashoffset:0}}
.p1{animation-delay:.25s}.p2{animation-delay:.5s}.p3{animation-delay:1.2s}.p4{animation-delay:1.45s}.p5{animation-delay:1.7s}
.soma{animation:soma 2.4s infinite}@keyframes soma{0%,40%{fill-opacity:.22}55%{fill-opacity:.85}80%,100%{fill-opacity:.22}}
.s3,.s4{animation-delay:.9s}
.ripple{transform-box:fill-box;transform-origin:center;opacity:0;animation:ripple 2.4s ease-out infinite}.q{animation-delay:.6s}
@keyframes ripple{0%{transform:scale(.6);opacity:.9}70%,100%{transform:scale(3.2);opacity:0}}
.belt{animation:belt 2.8s linear infinite}@keyframes belt{to{transform:translateX(80px)}}
.blink{animation:blink 2.8s infinite}@keyframes blink{0%,30%{stroke-opacity:1}45%{stroke-opacity:.25}60%,100%{stroke-opacity:1}}
""")
    for i, r in enumerate(RESEARCH):
        gx, gy = (i % 2) * (fw + gap), (i // 2) * (fh + gap)
        hue = C[r["hue"]]
        art = ILLUSTRATIONS[r["key"]](C, d, str(i))
        lines = body.wrap(r["body"], fw - 44)
        if len(lines) > 3:
            print(f"  ! research '{r['key']}' body wraps to {len(lines)} lines", file=sys.stderr)
        parts = [f'<g transform="translate({gx} {gy})">',
                 f'<path d="{brackets(1, 1, fw - 2, fh - 2)}" fill="none" stroke="{hue}" stroke-width="2"/>',
                 f'<g transform="translate(22 22)">{art}</g>',
                 d.text(22, 184, r["title"], title, C["fg"])]
        parts += [d.text(22, 212 + j * 22.5, ln, body, C["muted"]) for j, ln in enumerate(lines)]
        parts.append(d.text(22, 284, r["lab"], lab, hue))
        parts.append("</g>")
        d.add(*parts)
    write(OUT / theme / "research.svg", d.render())


# --------------------------------------------------------------------------- patents

PATENTS = [
    dict(fig="FIG. 1", head="USPTO 63/970,607", id="Application ID 2026-025",
         title="Detecting optical injection attacks on cameras by harnessing adversarial optical physics"),
    dict(fig="FIG. 2", head="USPTO 64/070,198", id="Application ID 2026-070",
         title="Lens Shield: an out-of-view laser and optical injection shield"),
    dict(fig="FIG. 3", head="Application in process", id="Application ID pending",
         title="StrayRestore: recovering camera feeds after rolling-shutter injection attacks"),
]


def _arrow(x1, y1, x2, y2, hl=7):
    a = math.atan2(y2 - y1, x2 - x1)
    l1 = (x2 - hl * math.cos(a - .45), y2 - hl * math.sin(a - .45))
    l2 = (x2 - hl * math.cos(a + .45), y2 - hl * math.sin(a + .45))
    return (f"M{num(x1)} {num(y1)}L{num(x2)} {num(y2)}M{num(l1[0])} {num(l1[1])}L{num(x2)} {num(y2)}"
            f"L{num(l2[0])} {num(l2[1])}")


def fig_detect(C, d, ref):
    k = C["pink"]
    curve = "M184 208" + "".join(
        f"L{num(184 + i * 2)} {num(208 - 6 * math.sin(i / 3.2) ** 2 - 70 * math.exp(-((i - 26) / 2.6) ** 2))}" for i in range(1, 46))
    return [
        ("p", rect_path(40, 82, 70, 46, 4)), ("p", "M54 82V74H78V82"), ("p", rect_path(110, 90, 18, 30, 2)),
        ("k", _arrow(258, 52, 130, 100, 9)),
        ("p", "M75 130V150"), ("p", _arrow(75, 140, 75, 152)),
        ("p", rect_path(30, 156, 124, 84, 3)),
        ("p", "M30 220L62 196L84 212L112 186L154 222"),
        ("k", "M118 176m-6 0a6 6 0 1 0 12 0a6 6 0 1 0 -12 0"),
        ("k", "M118 176m-14 0a14 14 0 1 0 28 0a14 14 0 1 0 -28 0"),
        ("k", "M34 176H150"),
        ("p", _arrow(160, 198, 178, 198)),
        ("p", "M184 160V210H272"), ("p", curve),
        ("k", "M236 130m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0"), ("k", "M232 130l3 3l6 -6"),
        ("p", "M226 138L236 132"),
    ], [ref(30, 66, "102", 48, 80), ref(264, 84, "104", 222, 66), ref(18, 252, "106", 34, 238), ref(276, 228, "108", 258, 210)]


def fig_shield(C, d, ref):
    k = C["pink"]
    hood_top = lambda x: 126 - (x - 146) * 30 / 86
    hood_bot = lambda x: 176 + (x - 146) * 30 / 86
    baffles = "".join(f"M{x} {num(hood_top(x))}V{num(hood_bot(x))}" for x in (172, 196, 220))
    return [
        ("p", rect_path(28, 118, 92, 64, 5)), ("p", "M48 118V106H80V118"), ("p", rect_path(120, 130, 26, 42, 2)),
        ("p", "M146 126L232 96V206L146 176"), ("p", baffles),
        ("k", _arrow(286, 34, 236, 92, 9)), ("k", "M228 92l-8 -6M230 86l-2 -10M234 84l6 -8"),
        ("k", _arrow(292, 140, 240, 140)), ("k", _arrow(292, 151, 240, 151)), ("k", _arrow(292, 162, 240, 162)),
    ], [ref(196, 244, "202", 206, 202), ref(184, 70, "204", 196, 112), ref(270, 22, "206", 262, 56), ref(38, 210, "208", 60, 182)]


def fig_restore(C, d, ref):
    k = C["pink"]
    def scene(x):
        return [("p", rect_path(x, 70, 86, 70, 3)), ("p", f"M{x} 124L{x + 22} 104L{x + 40} 118L{x + 60} 96L{x + 86} 126"),
                ("p", f"M{x + 64} 86m-7 0a7 7 0 1 0 14 0a7 7 0 1 0 -14 0")]
    hatch = "".join(f"M{18 + i * 8} {y}l8 -8" for y in (90, 112, 134) for i in range(10))
    rows = "".join(f"M{num(44 + i * 14)} {196 + i * 9}H{num(148 + i * 14)}" for i in range(6))
    return (scene(18) + [
        ("h", hatch),
        ("p", _arrow(108, 105, 124, 105)), ("p", rect_path(126, 81, 46, 48, 4)),
        ("p", "M132 105" + "".join(f"L{num(132 + i)} {num(105 - 11 * math.sin(i / 34 * 4 * math.pi))}" for i in range(1, 35))),
        ("p", _arrow(176, 105, 192, 105))] + scene(196) + [
        ("p", rows), ("p", _arrow(36, 252, 270, 252)),
        ("k", "M128 186V252"),
    ]), [ref(22, 58, "302", 40, 70), ref(139, 66, "304", 150, 80), ref(260, 58, "306", 250, 70), ref(246, 216, "308", 222, 236)]


def build_patents(theme: str):
    C = THEMES[theme]
    W, H = 1000, 470
    pw, ph = 300, 300
    d = Doc(W, H, "Patents",
            "Three patent applications drawn as patent figures: optical injection detection, the Lens Shield "
            "attachment, and StrayRestore feed recovery.")
    head = Style("ph", 12, 100, 560, 0.4)
    fig = Style("fg", 15, 112, 700, 1.6)
    refst = Style("rf", 12, 100, 520, 0.2)
    title = Style("tt", 17, 100, 680, 0)
    idst = Style("id", 13.5, 100, 480, 0.2)
    d.css.append("""
.dw{animation:draw 1.9s cubic-bezier(.45,0,.2,1) both}
@keyframes draw{0%{stroke-dasharray:1 1;stroke-dashoffset:1}99%{stroke-dasharray:1 1;stroke-dashoffset:0}100%{stroke-dasharray:none;stroke-dashoffset:0}}
.pl1 .dw{animation-delay:.35s}.pl2 .dw{animation-delay:.7s}
.rv{animation:fade .8s both}.pl0 .rv{animation-delay:1.4s}.pl1 .rv{animation-delay:1.75s}.pl2 .rv{animation-delay:2.1s}
@keyframes fade{from{opacity:0}to{opacity:1}}
""")
    figs = [fig_detect, fig_shield, fig_restore]
    for i, p in enumerate(PATENTS):
        gx = i * (pw + 50)
        refs = []

        def ref(tx, ty, label, lx, ly):
            refs.append((tx, ty, label, lx, ly))
            return None

        strokes, _ = figs[i](C, d, ref)
        out = [f'<g class="pl{i}" transform="translate({gx} 0)">',
               f'<rect x=".5" y=".5" width="{pw - 1}" height="{ph - 1}" rx="3" fill="none" stroke="{C["rule"]}"/>',
               d.text(pw / 2, 24, p["head"], head, C["muted"], anchor="middle")]
        for kind, path in strokes:
            col = C["pink"] if kind == "k" else C["fg"]
            sw = "1" if kind == "h" else "1.6"
            out.append(f'<path class="dw" pathLength="1" d="{path}" fill="none" stroke="{col}" stroke-width="{sw}" '
                       f'stroke-linecap="round" stroke-linejoin="round"/>')
        for tx, ty, label, lx, ly in refs:
            tw = refst.width(label)
            if lx > tx + tw:
                ax, ay = tx + tw + 3, ty - 4
            elif lx < tx:
                ax, ay = tx - 3, ty - 4
            else:
                ax, ay = tx + tw / 2, (ty + 3 if ly > ty else ty - 13)
            out.append(f'<path class="rv" d="M{num(ax)} {num(ay)}Q{num((ax + lx) / 2 + 6)} {num((ay + ly) / 2 - 6)} {num(lx)} {num(ly)}" '
                       f'fill="none" stroke="{C["muted"]}" stroke-width="1"/>')
            out.append(d.text(tx, ty, label, refst, C["muted"], cls="rv"))
        out.append(d.text(pw / 2, 284, p["fig"], fig, C["fg"], anchor="middle"))
        lines = title.wrap(p["title"], pw - 4)
        out += [d.text(0, 334 + j * 23, ln, title, C["fg"]) for j, ln in enumerate(lines)]
        out.append(d.text(0, 334 + len(lines) * 23 + 8, p["id"], idst, C["muted"]))
        out.append("</g>")
        d.add(*out)
    write(OUT / theme / "patents.svg", d.render())


# --------------------------------------------------------------------------- journey

JOURNEY = [
    ("2014", "violet", ["1st place, district round", "National Talent Hunt"]),
    ("2019", "violet", ["Champion, NEUB internal", "programming contest"]),
    ("2020", "violet", ["ICPC Bangladesh regional,", "national rank 90"]),
    ("2021", "violet", ["Code Jam world 1677,", "national 13", "ICPC rank 103", "Hackathon 2nd runner-up"]),
    ("2022", "green", ["ICPC rank 141", "First AIChE talk on soft", "robots, Phoenix"]),
    ("2023", "violet", ["Software engineer (QA lead)", "at Dorik", "AIChE & CompFlu talks"]),
    ("2024", "green", ["APS DSOFT talk,", "Minneapolis"]),
    ("2025", "pink", ["Smart-camera flaws in", "Auburn News", "ED2 dataset, IEEE DataPort"]),
    ("2026", "pink", ["3 patent applications", "Paper in Soft Robotics", "Biohybrid robots NSF news"]),
]


def build_journey(theme: str):
    C = THEMES[theme]
    above = max(len(j[2]) for i, j in enumerate(JOURNEY) if i % 2 == 0)
    below = max(len(j[2]) for i, j in enumerate(JOURNEY) if i % 2 == 1)
    ay = 16 + 77 + 19 * above
    W, H = 1000, ay + 61 + 19 * below + 18
    x0, step = 46, 94.5
    d = Doc(W, H, "Journey",
            "Timeline from 2014 to 2026: programming contests and ICPC, software engineering at Dorik, "
            "soft-robotics talks, camera security research, a dataset, a journal paper and three patent applications.")
    year = Style("yr", 19, 118, 760, 0)
    line = Style("jl", 13.5, 100, 450, 0.1)
    for i in range(len(JOURNEY)):
        cls_delay = f".n{i}{{animation-delay:{num(1.2 + i * 0.5)}s}}.l{i}{{animation-delay:{num(.2 + i * .14)}s}}"
        d.css.append(cls_delay)
    d.css.append(f"""
.lbl{{animation:rise .9s cubic-bezier(.2,.7,.2,1) both}}
@keyframes rise{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
.pulse{{animation:travel 7s 1.2s cubic-bezier(.4,0,.6,1) infinite both}}
@keyframes travel{{0%{{transform:translateX(0);opacity:0}}3%{{opacity:1}}62%{{transform:translateX({num(step * 8)}px);opacity:1}}68%,100%{{transform:translateX({num(step * 8)}px);opacity:0}}}}
.node{{transform-box:fill-box;transform-origin:center;animation:ping 7s infinite both}}
@keyframes ping{{0%{{transform:scale(1);fill-opacity:.25}}3%{{transform:scale(1.9);fill-opacity:1}}14%,100%{{transform:scale(1);fill-opacity:.25}}}}
""")
    d.defs.append(f'<radialGradient id="pg"><stop offset="0" stop-color="#fff"/><stop offset=".3" stop-color="{C["pink"]}"/>'
                  f'<stop offset="1" stop-color="{C["pink"]}" stop-opacity="0"/></radialGradient>'
                  f'<linearGradient id="trail" x1="0" x2="1"><stop offset="0" stop-color="{C["pink"]}" stop-opacity="0"/>'
                  f'<stop offset="1" stop-color="{C["pink"]}" stop-opacity=".8"/></linearGradient>')
    xs = [x0 + i * step for i in range(len(JOURNEY))]
    gap_mid = (xs[0] + xs[1]) / 2
    d.add(f'<path d="M{num(xs[0])} {ay}H{num(gap_mid - 7)}M{num(gap_mid + 7)} {ay}H{num(xs[-1] + 40)}" stroke="{C["rule"]}" stroke-width="2"/>',
          f'<path d="M{num(gap_mid - 11)} {ay + 8}l8 -16M{num(gap_mid - 3)} {ay + 8}l8 -16" stroke="{C["faint"]}" stroke-width="1.5"/>',
          f'<path d="M{num(xs[-1] + 34)} {ay - 5}l8 5l-8 5" fill="none" stroke="{C["rule"]}" stroke-width="2"/>')
    for i, (yr, hue, lines) in enumerate(JOURNEY):
        x, c = xs[i], C[hue]
        above = i % 2 == 0
        for ln in lines:
            if line.width(ln) > 2 * step - 14:
                print(f"  ! journey {yr} line too wide: {ln}", file=sys.stderr)
        if above:
            top = ay - 34 - len(lines) * 19 - 26
            stem = f"M{num(x)} {num(top - 15)}V{ay - 9}"
            ty = top
        else:
            ty = ay + 52
            stem = f"M{num(x)} {ay + 9}V{num(ty + 8 + len(lines) * 19)}"
        g = [f'<g class="lbl l{i}">', f'<path d="{stem}" stroke="{c}" stroke-opacity=".55" stroke-width="1.2"/>',
             d.text(x + 10, ty, yr, year, C["fg"])]
        g += [d.text(x + 10, ty + 22 + j * 19, ln, line, C["muted"]) for j, ln in enumerate(lines)]
        g.append("</g>")
        d.add(*g)
        d.add(f'<circle cx="{num(x)}" cy="{ay}" r="9" fill="{C["surface"]}" stroke="{c}" stroke-width="2"/>',
              f'<circle class="node n{i}" cx="{num(x)}" cy="{ay}" r="4.5" fill="{c}" fill-opacity=".25"/>')
    d.add(f'<g class="pulse"><rect x="{num(xs[0] - 60)}" y="{ay - 1.5}" width="60" height="3" fill="url(#trail)"/>'
          f'<circle cx="{num(xs[0])}" cy="{ay}" r="9" fill="url(#pg)"/></g>')
    write(OUT / theme / "journey.svg", d.render())


# --------------------------------------------------------------------------- activity (GitHub data)

GQL = "https://api.github.com/graphql"


def gql(token: str, query: str, variables: dict) -> dict:
    req = urllib.request.Request(GQL, data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Authorization": f"bearer {token}", "User-Agent": "profile-readme-build"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    if "errors" in out:
        raise RuntimeError(out["errors"])
    return out["data"]


def fetch_stats(token: str) -> dict:
    q_user = """query($login:String!){user(login:$login){createdAt followers{totalCount}
      repositories(ownerAffiliations:OWNER,privacy:PUBLIC){totalCount}
      contributionsCollection{contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}}}}"""
    q_repos = """query($login:String!,$after:String){user(login:$login){repositories(ownerAffiliations:OWNER,privacy:PUBLIC,
      isFork:false,first:100,after:$after){pageInfo{hasNextPage endCursor}nodes{stargazerCount
      languages(first:12,orderBy:{field:SIZE,direction:DESC}){edges{size node{name color}}}}}}}"""
    u = gql(token, q_user, {"login": USER})["user"]
    stars, langs, after = 0, {}, None
    while True:
        page = gql(token, q_repos, {"login": USER, "after": after})["user"]["repositories"]
        for n in page["nodes"]:
            stars += n["stargazerCount"]
            for e in n["languages"]["edges"]:
                name = e["node"]["name"]
                if name == "Jupyter Notebook":
                    continue
                langs.setdefault(name, [e["node"]["color"] or "#8b949e", 0])[1] += e["size"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    cal = u["contributionsCollection"]["contributionCalendar"]
    days = [(dd["date"], dd["contributionCount"]) for w in cal["weeks"] for dd in w["contributionDays"]]
    weeks = [[w["contributionDays"][0]["date"], sum(x["contributionCount"] for x in w["contributionDays"])] for w in cal["weeks"]]
    longest = run = 0
    for _, c in days:
        run = run + 1 if c else 0
        longest = max(longest, run)
    current = 0
    tail = days[:-1] if days and days[-1][1] == 0 else days  # today may still be empty
    for _, c in reversed(tail):
        if not c:
            break
        current += 1
    return dict(
        generated=days[-1][0] if days else dt.date.today().isoformat(),
        since=u["createdAt"][:4], followers=u["followers"]["totalCount"], repos=u["repositories"]["totalCount"],
        stars=stars, contributions=cal["totalContributions"], longest=longest, current=current, weeks=weeks,
        languages=sorted(([k, v[0], v[1]] for k, v in langs.items()), key=lambda x: -x[2]),
    )


def monotone(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    n = len(points)
    dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
    m = [(ys[i + 1] - ys[i]) / dx[i] for i in range(n - 1)]
    t = [m[0]] + [0 if m[i - 1] * m[i] <= 0 else (m[i - 1] + m[i]) / 2 for i in range(1, n - 1)] + [m[-1]]
    for i in range(n - 1):
        if m[i] == 0:
            t[i] = t[i + 1] = 0
        else:
            a, b = t[i] / m[i], t[i + 1] / m[i]
            s = a * a + b * b
            if s > 9:
                k = 3 / math.sqrt(s)
                t[i], t[i + 1] = k * a * m[i], k * b * m[i]
    path = f"M{num(xs[0])} {num(ys[0])}"
    for i in range(n - 1):
        path += (f"C{num(xs[i] + dx[i] / 3)} {num(ys[i] + t[i] * dx[i] / 3)} "
                 f"{num(xs[i + 1] - dx[i] / 3)} {num(ys[i + 1] - t[i + 1] * dx[i] / 3)} {num(xs[i + 1])} {num(ys[i + 1])}")
    return path


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def build_activity(theme: str, s: dict):
    C = THEMES[theme]
    W, H = 1000, 404
    sx0, sy0, sw, sh = 0, 0, 1000, 236
    d = Doc(W, H, "GitHub activity",
            f"{s['contributions']} contributions in the last year, longest streak {s['longest']} days, "
            f"{s['repos']} public repositories, {s['stars']} stars, {s['followers']} followers.")
    small = Style("sm", 13, 100, 560, 0.2)
    tick = Style("tk", 11.5, 100, 500, 0.3)
    lbl = Style("ml", 13, 100, 480, 0.15)
    val = Style("mv", 23, 112, 720, 0)
    lang = Style("lg", 13, 100, 500, 0.1)
    d.css.append("""
.tr{animation:draw 2.6s .2s cubic-bezier(.45,0,.2,1) both}
@keyframes draw{0%{stroke-dasharray:1 1;stroke-dashoffset:1}99%{stroke-dasharray:1 1;stroke-dashoffset:0}100%{stroke-dasharray:none;stroke-dashoffset:0}}
.cur{animation:cur 6s 2.8s linear infinite both}
@keyframes cur{0%{transform:translateX(0);opacity:0}4%{opacity:.9}96%{opacity:.9}100%{transform:translateX(950px);opacity:0}}
.pk{transform-box:fill-box;transform-origin:center;animation:pk 2s 2.8s ease-out infinite both}
@keyframes pk{0%{transform:scale(1);opacity:.9}100%{transform:scale(3.4);opacity:0}}
.bar{transform-box:fill-box;transform-origin:left;animation:grow 1.6s .4s cubic-bezier(.2,.7,.2,1) both}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
""")
    # scope screen + graticule
    px0, px1, py0, py1 = 26, 974, 44, 200
    grid = "".join(f"M{num(px0 + i * (px1 - px0) / 10)} {py0}V{py1}" for i in range(11))
    grid += "".join(f"M{px0} {num(py0 + j * (py1 - py0) / 4)}H{px1}" for j in range(5))
    minor = "".join(f"M{num(px0 + i * (px1 - px0) / 50)} {num((py0 + py1) / 2 - 3)}v6" for i in range(51))
    d.add(f'<rect x="{sx0 + .5}" y="{sy0 + .5}" width="{sw - 1}" height="{sh - 1}" rx="12" fill="{C["surface"]}" stroke="{C["rule"]}"/>',
          f'<path d="{grid}" stroke="{C["rule"]}" stroke-dasharray="2 4"/>',
          f'<path d="{minor}" stroke="{C["rule"]}"/>')
    weeks = s["weeks"]
    counts = [w[1] for w in weeks]
    peak = max(counts) or 1
    pts = [(px0 + i * (px1 - px0) / (len(counts) - 1), py1 - (c / peak) * (py1 - py0 - 6)) for i, c in enumerate(counts)]
    trace = monotone(pts)
    pi = counts.index(max(counts))
    first = dt.date.fromisoformat(weeks[0][0])
    last = dt.date.fromisoformat(s["generated"])
    d.add(d.text(px0, 30, "Contributions per week, last 12 months", small, C["amber"]),
          d.text(px1, 30, f"{first:%b %Y} to {last:%b %Y}", small, C["muted"], anchor="end"))
    # month ticks
    seen = set()
    for i, (wd, _) in enumerate(weeks):
        day = dt.date.fromisoformat(wd)
        key = (day.year, day.month)
        if i and key not in seen and day.day <= 7:
            d.add(d.text(pts[i][0], 222, f"{day:%b}", tick, C["faint"], anchor="middle"))
        seen.add(key)
    d.add(f'<path d="{trace}" fill="none" stroke="{C["amber"]}" stroke-width="7" stroke-opacity=".14" stroke-linejoin="round"/>',
          f'<path class="tr" pathLength="1" d="{trace}" fill="none" stroke="{C["amber"]}" stroke-width="2.2" stroke-linejoin="round"/>')
    pkx, pky = pts[pi]
    peak_label = f"peak {counts[pi]}, week of {dt.date.fromisoformat(weeks[pi][0]):%b} {dt.date.fromisoformat(weeks[pi][0]).day}"
    anchor = "end" if pkx > 760 else "start"
    lx = pkx - 12 if anchor == "end" else pkx + 12
    d.add(f'<circle cx="{num(pkx)}" cy="{num(pky)}" r="4.5" fill="{C["pink"]}"/>',
          f'<circle class="pk" cx="{num(pkx)}" cy="{num(pky)}" r="4.5" fill="none" stroke="{C["pink"]}" stroke-width="1.5"/>',
          d.text(lx, max(pky - 2, 62), peak_label, small, C["pink"], anchor=anchor))
    d.add(f'<g class="cur"><rect x="{px0}" y="{py0}" width="1.5" height="{py1 - py0}" fill="{C["amber"]}" opacity=".7"/>'
          f'<rect x="{px0 - 24}" y="{py0}" width="24" height="{py1 - py0}" fill="{C["amber"]}" opacity=".06"/></g>')

    # measurement row
    cells = [("Contributions, last year", f"{s['contributions']:,}"), ("Longest streak", plural(s["longest"], "day")),
             ("Current streak", plural(s["current"], "day")), ("Public repositories", f"{s['repos']}"),
             ("Stars earned", f"{s['stars']}"), ("Followers", f"{s['followers']}")]
    cw = W / len(cells)
    for i, (k, v) in enumerate(cells):
        x = i * cw
        d.add(f'<path d="M{num(x + (0 if i == 0 else 10))} 258H{num(x + cw - 10 if i < len(cells) - 1 else W)}" stroke="{C["rule"]}"/>',
              d.text(x + (0 if i == 0 else 10), 280, k, lbl, C["muted"]),
              d.text(x + (0 if i == 0 else 10), 310, v, val, C["fg"]))

    # language spectrum
    langs = s["languages"]
    total = sum(x[2] for x in langs) or 1
    top = langs[:7]
    rest = total - sum(x[2] for x in top)
    if rest > 0:
        top = top + [["Other", C["faint"], rest]]
    d.add(d.text(0, 350, "Languages in public repositories, by code size", lbl, C["muted"]))
    x = 0.0
    segs = []
    d.defs.append(f'<clipPath id="barclip"><rect x="0" y="362" width="{W}" height="9" rx="4.5"/></clipPath>')
    for name, color, size in top:
        w = W * size / total
        segs.append(f'<rect x="{num(x)}" y="362" width="{num(max(w - 2, 1))}" height="9" fill="{color}"/>')
        x += w
    d.add(f'<g clip-path="url(#barclip)"><g class="bar">{"".join(segs)}</g></g>')
    x = 0.0
    for name, color, size in top:
        label = f"{name} {100 * size / total:.1f}%"
        d.add(f'<circle cx="{num(x + 5)}" cy="391" r="5" fill="{color}"/>', d.text(x + 15, 396, label, lang, C["muted"]))
        x += 15 + lang.width(label) + 22
    if x > W:
        print("  ! language legend overflows", file=sys.stderr)
    write(OUT / theme / "activity.svg", d.render())


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stats", action="store_true", help="refresh scripts/stats.json from the GitHub API")
    ap.add_argument("--only", choices=["hero", "headers", "research", "patents", "journey", "activity"])
    args = ap.parse_args()

    if args.stats:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("GH_TOKEN or GITHUB_TOKEN is required for --stats")
        STATS.write_text(json.dumps(fetch_stats(token), indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"  {STATS.relative_to(ROOT)} refreshed")

    want = lambda k: args.only in (None, k)
    if want("hero"):
        build_hero()
    for theme in THEMES:
        if want("headers"):
            for slug, title in HEADERS.items():
                build_header(slug, title, theme)
        if want("research"):
            build_research(theme)
        if want("patents"):
            build_patents(theme)
        if want("journey"):
            build_journey(theme)
        if want("activity"):
            if STATS.exists():
                build_activity(theme, json.loads(STATS.read_text(encoding="utf-8")))
            else:
                print("  skipped activity card: run with --stats first", file=sys.stderr)


if __name__ == "__main__":
    main()

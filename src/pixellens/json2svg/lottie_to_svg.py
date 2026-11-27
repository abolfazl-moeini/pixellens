#!/usr/bin/env python3
"""
Convert a Lottie JSON file to a standalone single-file JS-based Animated SVG.
Supports Trim Paths, Parenting, cubic-bezier keyframe easing, transforms, and layer ip/op.

Usage:
    python3 lottie_to_svg.py input.json output.svg
"""

import argparse
import json
import os
import sys


def path_to_d(k):
    v = k["v"]
    iv = k["i"]
    ov = k["o"]
    c = k.get("c", False)
    n = len(v)
    if n == 0:
        return ""
    parts = [f"M{v[0][0]:.3f},{v[0][1]:.3f}"]
    for j in range(n - 1):
        c1x, c1y = v[j][0] + ov[j][0], v[j][1] + ov[j][1]
        c2x, c2y = v[j + 1][0] + iv[j + 1][0], v[j + 1][1] + iv[j + 1][1]
        ex, ey = v[j + 1][0], v[j + 1][1]
        parts.append(f"C{c1x:.3f},{c1y:.3f} {c2x:.3f},{c2y:.3f} {ex:.3f},{ey:.3f}")
    if c:
        c1x, c1y = v[n - 1][0] + ov[n - 1][0], v[n - 1][1] + ov[n - 1][1]
        c2x, c2y = v[0][0] + iv[0][0], v[0][1] + iv[0][1]
        ex, ey = v[0][0], v[0][1]
        parts.append(f"C{c1x:.3f},{c1y:.3f} {c2x:.3f},{c2y:.3f} {ex:.3f},{ey:.3f}Z")
    return "".join(parts)


def is_animated(prop):
    return isinstance(prop, dict) and prop.get("a") == 1


def compact_prop(prop, default_val):
    if prop is None:
        return default_val
    if not is_animated(prop):
        return prop.get("k", default_val)
    kfs = []
    for kf in prop["k"]:
        entry = {"t": kf["t"], "s": kf["s"]}
        if "o" in kf and "i" in kf:
            ox, oy = kf["o"]["x"], kf["o"]["y"]
            ix, iy = kf["i"]["x"], kf["i"]["y"]
            if isinstance(ox, list):
                ox, oy, ix, iy = ox[0], oy[0], ix[0], iy[0]
            entry["b"] = [ox, oy, ix, iy]
        kfs.append(entry)
    return {"a": 1, "k": kfs}


def extract_tr(ks):
    return {
        "a": compact_prop(ks.get("a"), [0, 0]),
        "p": compact_prop(ks.get("p"), [0, 0]),
        "s": compact_prop(ks.get("s"), [100, 100]),
        "r": compact_prop(ks.get("r"), 0),
        "o": compact_prop(ks.get("o"), 100),
    }


def has_animated_tr(tr):
    return any(isinstance(tr[k], dict) and tr[k].get("a") == 1 for k in ("a", "p", "s", "r", "o"))


def static_tr_str(tr):
    px, py = tr["p"][0], tr["p"][1]
    ax, ay = tr["a"][0], tr["a"][1]
    sx, sy = tr["s"][0] / 100.0, tr["s"][1] / 100.0
    r = tr["r"]
    parts = []
    if px != 0 or py != 0:
        parts.append(f"translate({px:.3f},{py:.3f})")
    if r != 0:
        parts.append(f"rotate({r:.3f})")
    if sx != 1 or sy != 1:
        parts.append(f"scale({sx:.4f},{sy:.4f})")
    if ax != 0 or ay != 0:
        parts.append(f"translate({-ax:.3f},{-ay:.3f})")
    return " ".join(parts)


def convert_lottie_to_svg(input_path, output_path):
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    anim_nodes = []
    node_counter = [0]

    def next_id(prefix="n"):
        node_counter[0] += 1
        return f"{prefix}{node_counter[0]}"

    def build_shape_items(items, inherited_stroke=None, inherited_fill=None, inherited_trim=None):
        local_stroke = inherited_stroke
        local_fill = inherited_fill
        local_trim = inherited_trim
        local_tr = None
        for it in items:
            ty = it.get("ty")
            if ty == "st":
                c = it["c"]["k"]
                rgb = f"rgb({int(round(c[0]*255))},{int(round(c[1]*255))},{int(round(c[2]*255))})"
                w = it["w"]["k"]
                lc = {1: "butt", 2: "round", 3: "square"}.get(it.get("lc", 2), "round")
                lj = {1: "miter", 2: "round", 3: "bevel"}.get(it.get("lj", 2), "round")
                local_stroke = {"c": rgb, "w": w, "lc": lc, "lj": lj}
            elif ty == "fl":
                c = it["c"]["k"]
                local_fill = f"rgb({int(round(c[0]*255))},{int(round(c[1]*255))},{int(round(c[2]*255))})"
            elif ty == "tm":
                local_trim = {
                    "s": compact_prop(it.get("s"), 0),
                    "e": compact_prop(it.get("e"), 100),
                    "o": compact_prop(it.get("o"), 0),
                }
            elif ty == "tr":
                local_tr = extract_tr(it)

        children_svg = []
        for it in items:
            ty = it.get("ty")
            if ty == "gr":
                children_svg.append(build_shape_items(it["it"], local_stroke, local_fill, local_trim))
            elif ty == "sh":
                d = path_to_d(it["ks"]["k"])
                attrs = [f'd="{d}"', f'fill="{local_fill or "none"}"']
                if local_stroke:
                    attrs.extend([
                        f'stroke="{local_stroke["c"]}"',
                        f'stroke-width="{local_stroke["w"]}"',
                        f'stroke-linecap="{local_stroke["lc"]}"',
                        f'stroke-linejoin="{local_stroke["lj"]}"',
                    ])
                if local_trim is not None:
                    pid = next_id("p")
                    attrs.insert(0, f'id="{pid}"')
                    anim_nodes.append({"id": pid, "type": "tm", "tm": local_trim})
                children_svg.append(f'<path {" ".join(attrs)}/>')

        inner = "".join(children_svg)
        if local_tr is not None:
            if has_animated_tr(local_tr):
                gid = next_id("g")
                anim_nodes.append({"id": gid, "type": "tr", "tr": local_tr})
                return f'<g id="{gid}">{inner}</g>'
            tstr = static_tr_str(local_tr)
            if tstr:
                return f'<g transform="{tstr}">{inner}</g>'
        return inner

    layer_trs = {
        l["ind"]: {"tr": extract_tr(l["ks"]), "parent": l.get("parent")}
        for l in data["layers"]
    }

    layer_svgs = []
    for l in reversed(data["layers"]):
        if l["ty"] == 3:
            continue
        lid = next_id("L")
        inner = build_shape_items(l.get("shapes", []))
        anim_nodes.append({
            "id": lid,
            "type": "layer",
            "ind": l["ind"],
            "ip": l["ip"],
            "op": l["op"],
        })
        layer_svgs.append(f'<g id="{lid}">{inner}</g>')

    payload = {
        "fr": data["fr"],
        "ip": data["ip"],
        "op": data["op"],
        "layers": layer_trs,
        "nodes": anim_nodes,
    }
    payload_json = json.dumps(payload, separators=(",", ":"))

    js_code = f"""(function(){{
const D={payload_json};
const svg=(document.currentScript&&document.currentScript.ownerSVGElement)||document.documentElement;
const byId=id=>svg.querySelector('#'+id);

function bezier(t,x1,y1,x2,y2){{
  if(x1===y1&&x2===y2)return t;
  let u=t;
  for(let k=0;k<8;k++){{
    const iu=1-u;
    const x=3*iu*iu*u*x1+3*iu*u*u*x2+u*u*u-t;
    if(Math.abs(x)<1e-6)break;
    const dx=3*iu*iu*x1+6*iu*u*(x2-x1)+3*u*u*(1-x2);
    if(Math.abs(dx)<1e-6)break;
    u-=x/dx;
    if(u<0)u=0;else if(u>1)u=1;
  }}
  const iu=1-u;
  return 3*iu*iu*u*y1+3*iu*u*u*y2+u*u*u;
}}

function evalProp(p,f){{
  if(!p||typeof p!=='object'||p.a!==1)return p;
  const k=p.k,n=k.length;
  if(f<=k[0].t)return k[0].s;
  if(f>=k[n-1].t)return k[n-1].s;
  let i=0;
  while(i<n-2&&f>=k[i+1].t)i++;
  const a=k[i],b=k[i+1];
  const dt=b.t-a.t;
  let t=dt>0?(f-a.t)/dt:0;
  if(a.b)t=bezier(t,a.b[0],a.b[1],a.b[2],a.b[3]);
  const s1=a.s,s2=b.s;
  if(Array.isArray(s1)){{
    const out=new Array(s1.length);
    for(let j=0;j<s1.length;j++)out[j]=s1[j]+(s2[j]-s1[j])*t;
    return out;
  }}
  return s1+(s2-s1)*t;
}}

function num(v){{return Array.isArray(v)?v[0]:v;}}

function trStr(tr,f){{
  const p=evalProp(tr.p,f),a=evalProp(tr.a,f),s=evalProp(tr.s,f),r=num(evalProp(tr.r,f));
  let out='';
  if(p[0]||p[1])out+='translate('+p[0].toFixed(3)+','+p[1].toFixed(3)+') ';
  if(r)out+='rotate('+r.toFixed(3)+') ';
  if(s[0]!==100||s[1]!==100)out+='scale('+(s[0]/100).toFixed(4)+','+(s[1]/100).toFixed(4)+') ';
  if(a[0]||a[1])out+='translate('+(-a[0]).toFixed(3)+','+(-a[1]).toFixed(3)+')';
  return out.trim();
}}

function layerTrStr(ind,f){{
  const chain=[];
  let cur=ind;
  while(cur!=null&&D.layers[cur]){{
    chain.unshift(D.layers[cur].tr);
    cur=D.layers[cur].parent;
  }}
  return chain.map(t=>trStr(t,f)).filter(Boolean).join(' ');
}}

const els=D.nodes.map(n=>{{
  const el=byId(n.id);
  const o={{n,el}};
  if(n.type==='tm')o.len=el.getTotalLength();
  return o;
}});

function render(f){{
  for(let idx=0;idx<els.length;idx++){{
    const {{n,el,len}}=els[idx];
    if(n.type==='layer'){{
      const vis=f>=n.ip&&f<n.op;
      el.style.display=vis?'':'none';
      if(vis)el.setAttribute('transform',layerTrStr(n.ind,f));
    }}else if(n.type==='tr'){{
      el.setAttribute('transform',trStr(n.tr,f));
    }}else if(n.type==='tm'){{
      let s=num(evalProp(n.tm.s,f))/100;
      let e=num(evalProp(n.tm.e,f))/100;
      let o=(num(evalProp(n.tm.o,f))%360)/360;
      if(o<0)o+=1;
      const a=Math.min(s,e),b=Math.max(s,e),seg=b-a;
      if(seg<=0.0005){{
        el.style.display='none';
      }}else{{
        el.style.display='';
        if(seg>=0.9995){{
          el.removeAttribute('stroke-dasharray');
          el.removeAttribute('stroke-dashoffset');
        }}else{{
          let p1=(a+o)%1;
          if(p1<0)p1+=1;
          const p2=p1+seg;
          if(p2<=1.0001){{
            const dLen=seg*len;
            el.setAttribute('stroke-dasharray',dLen.toFixed(3)+' '+(len*2).toFixed(3));
            el.setAttribute('stroke-dashoffset',(-p1*len).toFixed(3));
          }}else{{
            const d1=(p2-1)*len;
            const gap=(1-seg)*len;
            const d2=(1-p1)*len;
            el.setAttribute('stroke-dasharray',d1.toFixed(3)+' '+gap.toFixed(3)+' '+d2.toFixed(3)+' '+len.toFixed(3));
            el.setAttribute('stroke-dashoffset','0');
          }}
        }}
      }}
    }}
  }}
}}

let start=null;
const dur=(D.op-D.ip)/D.fr*1000;
function tick(ts){{
  if(start===null)start=ts;
  const elapsed=(ts-start)%dur;
  const f=D.ip+(elapsed/dur)*(D.op-D.ip);
  render(f);
  requestAnimationFrame(tick);
}}
render(D.ip);
requestAnimationFrame(tick);
}})();"""

    svg_out = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {data["w"]} {data["h"]}" '
        f'width="{data["w"]}" height="{data["h"]}">\n'
        f'{"".join(layer_svgs)}\n'
        f'<script><![CDATA[\n{js_code}\n]]></script>\n'
        '</svg>\n'
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(svg_out)
    print(f"Generated {output_path} ({len(svg_out):,} bytes)")


def main():
    parser = argparse.ArgumentParser(description="Convert Lottie JSON to standalone JS Animated SVG.")
    parser.add_argument("input", help="Path to input Lottie .json file")
    parser.add_argument("output", help="Path to output .svg file")
    args = parser.parse_args()
    convert_lottie_to_svg(args.input, args.output)


if __name__ == "__main__":
    main()

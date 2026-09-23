#!/usr/bin/env python3
"""生成 kb-kit 项目架构图 (draw.io / Next AI Draw.io 原生格式)。"""
import xml.sax.saxutils as xu
from pathlib import Path

cells = []
counter = [1]


def nx():
    counter[0] += 1
    return str(counter[0])


def box(x, y, w, h, style, label, parent="1"):
    cid = nx()
    cells.append(
        f'    <mxCell id="{cid}" value="{xu.escape(label)}" '
        f'style="{style}" vertex="1" parent="{parent}">'
        f'      <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />'
        f'    </mxCell>'
    )
    return cid


def rounded(x, y, w, h, fill, stroke, label):
    return box(x, y, w, h,
               f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
               f"fontColor=#1A1A2A;paddingLeft=8;paddingRight=8;textHeight=24;",
               label)


def layer_bg(x, y, w, h, fill, stroke, label):
    cid = box(x, y, w, h,
              f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
              f"strokeWidth=1;dashed=1;fontColor=#555566;",
              label)
    return cid


def arrow(src, tgt, style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;strokeColor=#5A6B7B;strokeWidth=2;"):
    cid = nx()
    cells.append(
        f'    <mxCell id="{cid}" style="{style}" edge="1" parent="1" source="{src}" target="{tgt}">'
        f'      <mxGeometry relative="1" as="geometry" />'
        f'    </mxCell>'
    )


def hline(x, y, w, style="html=1;endArrow=none;strokeColor=#5A6B7B;strokeWidth=1;dashed=1;"):
    cid = nx()
    cells.append(
        f'    <mxCell id="{cid}" value="" style="{style}" edge="1" parent="1">'
        f'      <mxGeometry x="{x}" y="{y}" width="{w}" height="0" as="geometry" />'
        f'    </mxCell>'
    )


# ---------- Title ----------
cells.append(f'    <mxCell id="title" value="{xu.escape("kb-kit 知识库项目 · 架构总览（插件化）")}" '
             f'style="text;html=1;align=center;fontSize=22;fontColor=#12132A;fontStyle=1;fontFamily=Helvetica;" '
             f'vertex="1" parent="1"><mxGeometry x="120" y="24" width="1180" height="40" as="geometry" /></mxCell>')

# ---------- Layer shells (left stack) ----------
LX = 120
LW = 720

bg1 = layer_bg(LX, 84, LW, 96, "#EAF4FC", "#3B82F6", "① 用户层  ·  CLI / Agent")
bg2 = layer_bg(LX, 196, LW, 72, "#EAF4FC", "#3B82F6", "② CLI 统一入口")
bg3 = layer_bg(LX, 288, LW, 96, "#ECFDF5", "#10B981", "③ 注册中心层 + 插件协议层（新建）")
bg4 = layer_bg(LX, 404, LW, 176, "#FEF3C7", "#F59E0B", "④ 插件实现层  pipeline/plugins/（25 个插件）")
bg5 = layer_bg(LX, 598, LW, 120, "#F1F5F9", "#64748B", "⑤ 业务逻辑层（原 pipeline 模块，保持原位不变）")
bg6 = layer_bg(LX, 736, LW, 72, "#F1F5F9", "#64748B", "⑥ 共享基础设施（不变）")

# L1 content
c_kb = rounded(LX + 28, 104, 320, 56, "#FFFFFF", "#93C5FD", "kb (bash)\nkb.cmd (Windows)")
c_dsh = rounded(LX + 372, 104, 320, 56, "#FFFFFF", "#93C5FD", "DeepSeek Harness Agent\n显式工具 kb_query + 回合前自动注入")
arrow(c_kb, bg2); arrow(c_dsh, bg3, "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;strokeColor=#3B82F6;strokeWidth=2;")

# L2
c_launch = rounded(LX + 120, 212, 480, 40, "#FFFFFF", "#93C5FD", "kb_launcher.py\n兼容映射表 COMPAT_MAP → 动态路由到注册中心")
arrow(c_launch, bg3)

# L3
c_reg = rounded(LX + 28, 308, 340, 56, "#FFFFFF", "#34D399", "plugin_registry.py\n发现 / 注册 / 拓扑排序 / 分发 / 生命周期")
c_proto = rounded(LX + 392, 308, 320, 56, "#FFFFFF", "#34D399", "plugin_base.py\nPluginBase · PluginContext(DI) · PluginMetadata")
arrow(c_reg, bg4); arrow(c_proto, bg4)

# L4 split
subA = rounded(LX + 28, 424, 328, 136, "#FFFFFF", "#FBBF24", "Agent 适配器插件\n· agent_md   · agent_json\n· agent_sqlite · agent_detect\n(封装 memory_ingest*.py)")
subB = rounded(LX + 372, 424, 340, 136, "#FFFFFF", "#FBBF24", "KB 功能模块插件\n· rag · intake · feedback\n· link · recall · healthcheck\n· dashboard · clean · validate · sync · ...")
arrow(c_reg, subA); arrow(c_reg, subB)

# L5
c_mods = rounded(LX + 28, 618, 664, 80, "#FFFFFF", "#CBD5E1",
                 "rag.py · intake_triage.py · feedback_loop.py · link_engine.py · recall_schedule.py\nmemory_ingest*.py · agent_registry.py · graph.py · sync.py · classify.py · rerank.py · ...（原模块不变）")
arrow(subA, c_mods); arrow(subB, c_mods)

# L6
c_inf = rounded(LX + 180, 754, 360, 40, "#FFFFFF", "#CBD5E1", "kb_common.py（公共工具）· state_manager.StateStore（状态管理）")
arrow(c_mods, c_inf)

# ---------- Right column ----------
RX = 900
RW = 556

# Vault
vault_bg = layer_bg(RX, 84, RW, 268, "#F5F3FF", "#8B5CF6", "KB Vault（Obsidian PARA 结构）")
vault_txt = (
    "<br/>🏠 知识库首页　|　📖 知识库管理方案<br/><br/>"
    "00 收件箱 → 01 如何开始<br/>"
    "10 项目 · 20 技术 · 30 决策日志<br/>"
    "40 资源库 · 50 模板 · 60 运营 · 70 知识治理<br/><br/>"
    "元数据 frontmatter：tags / status / domain / importance"
)
c_vault = box(RX + 24, 108, RW - 48, 220,
              f"rounded=1;whiteSpace=wrap;html=1;fillColor=FFFFFF;strokeColor=D1C4E9;fontColor=#1A1A2A;padding=8;",
              vault_txt)

# bidirectional DSH<->vault loop
arrow(c_vault, c_dsh, "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;startArrow=block;strokeColor=#8B5CF6;strokeWidth=2;dashed=1;")
loopsid = nx()
cells.append(f'    <mxCell id="{loopsid}" value="双向闭环：agent 记忆沉淀进 KB  ⟵ ⟶  KB 沉淀回流 agent" style="text;html=1;align=center;fontSize=11;fontColor=#6D28D9;" vertex="1" parent="1"><mxGeometry x="900" y="360" width="556" height="26" as="geometry" /></mxCell>')

# Growth engine
growth_bg = layer_bg(RX, 380, RW, 168, "#ECFEF3", "#059669", "成长引擎（pipeline/，有机体比喻）")
step1 = rounded(RX + 20, 400, 120, 56, "#FFFFFF", "#34D399", "intake_triage\n摄入 triage")
step2 = rounded(RX + 156, 400, 120, 56, "#FFFFFF", "#34D399", "feedback_loop\n命中信号")
step3 = rounded(RX + 292, 400, 120, 56, "#FFFFFF", "#34D399", "link_engine\n孤岛补链")
step4 = rounded(RX + 20, 476, 120, 56, "#FFFFFF", "#34D399", "recall_schedule\n回忆排期")
step5 = rounded(RX + 156, 476, 120, 56, "#FFFFFF", "#34D399", "kb_health\n健康度量")
step6 = rounded(RX + 292, 476, 120, 56, "#FFFFFF", "#34D399", "dashboard\n治理仪表盘")
arrow(step1, step2); arrow(step2, step3); arrow(step3, step4)
arrow(step4, step5); arrow(step5, step6); hline(RX + 420, 428, 96)
cells.append(f'    <mxCell id="{nx()}" value="⟳ 自循环治理" style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=open;strokeColor=#059669;strokeWidth=1;dashed=1;html=1;" edge="1" parent="1" source="{step6}" target="{step1}"><mxGeometry relative="1" as="geometry" /></mxCell>')

# kb-context-dsh
c_ctx = rounded(RX + 20, 568, RW - 40, 88, "#FFF7ED", "#FB923C",
                "kb-context-dsh（DeepSeek Harness 插件）\n• server-side cordis 钩子 agent/pre-step\n• 回合前自动注入 Top-N 命中（📚 本地知识库命中）\n• 显式 kb_query 工具　·　零 client UI　·　零额外运行依赖")

# ---------- footer note ----------
cells.append(f'    <mxCell id="note" value="{xu.escape("生成方式：draw.io 原生 mxGraph XML —— 可直接导入 draw.io，或粘贴进 Next AI Draw.io 编辑。架构依据：kb-plugin-architecture/design.md + kb-context-dsh/README.md + 📖-知识库管理方案.md")}" '
             f'style="text;html=1;align=center;fontSize=10;fontColor=#667;fontFamily=Helvetica;" vertex="1" parent="1"><mxGeometry x="120" y="832" width="1180" height="40" as="geometry" /></mxCell>')

# ---------- assemble ----------
header = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<mxfile host="app.diagrams.net" type="device"><diagram id="kb-kit-arch" name="kb-kit 架构图">\n'
    '  <mxGraphModel dx="1422" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1500" pageHeight="900" math="0" shadow="0">\n'
    '    <root>\n'
    '      <mxCell id="0" />\n'
    '      <mxCell id="1" parent="0" />\n'
)
footer = '    </root>\n  </mxGraphModel>\n</diagram>\n</mxfile>\n'

out = header + "\n".join(cells) + footer
path = str(Path(__file__).resolve().parent / "arch" / "kb-kit-pure-architecture.drawio")
with open(path, "w", encoding="utf-8") as f:
    f.write(out)
print("wrote", path, "cells:", len(cells))

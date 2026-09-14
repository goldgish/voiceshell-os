window.__ModuleLoader__.load({ id: "@local/dsh-voice-input", factory: (require) => { var module = { exports: {} }; var exports = module.exports;
"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/client/index.ts
var index_exports = {};
__export(index_exports, {
  apply: () => apply,
  inject: () => inject
});
module.exports = __toCommonJS(index_exports);

// src/client/VoiceButton.tsx
var import_react = require("react");
var import_jsx_runtime = require("react/jsx-runtime");
var ROOMY_WIDTH = 96;
function VoiceButton() {
  const [phase, setPhase] = (0, import_react.useState)("checking");
  const [roomy, setRoomy] = (0, import_react.useState)(false);
  const boxRef = (0, import_react.useRef)(null);
  (0, import_react.useEffect)(() => {
    let alive = true;
    const poll = async () => {
      try {
        const resp = await fetch("/voice/status");
        const data = await resp.json();
        if (alive) setPhase(data.online === true ? "online" : "offline");
      } catch {
        if (alive) setPhase("offline");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 5e3);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  (0, import_react.useEffect)(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setRoomy(el.clientWidth >= ROOMY_WIDTH);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const color = phase === "online" ? "#3fb950" : phase === "offline" ? "#8b949e" : "#d29922";
  const label = phase === "online" ? "\u8BED\u97F3\u5728\u7EBF" : phase === "offline" ? "\u8BED\u97F3\u79BB\u7EBF" : "\u8BED\u97F3\u2026";
  const title = phase === "online" ? "\u6309\u4F4F\u9065\u63A7\u5668\u8BED\u97F3\u952E\u8BF4\u8BDD\uFF0C\u677E\u624B\u5373\u53D1\u9001\u7ED9\u79D8\u4E66" : "\u8BED\u97F3\u8FD0\u884C\u65F6\u672A\u542F\u52A8\uFF1A\u53CC\u51FB\u4ED3\u5E93\u6839\u76EE\u5F55\u7684\u300C\u542F\u52A8.bat\u300D";
  return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
    "span",
    {
      ref: boxRef,
      title,
      style: {
        // width:100% 是为了让 clientWidth 等于这一格真正能用的宽度（不随文字有无而变），
        // 否则隐藏文字会让测量值缩小，跟 ResizeObserver 打回声。
        width: "100%",
        boxSizing: "border-box",
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "4px 8px",
        fontSize: 12,
        fontFamily: "sans-serif",
        color: "inherit",
        opacity: phase === "online" ? 1 : 0.6,
        cursor: "default"
      },
      children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
          "span",
          {
            style: {
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: color,
              boxShadow: phase === "online" ? `0 0 5px ${color}` : "none",
              flex: "none"
            }
          }
        ),
        roomy ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { style: { whiteSpace: "nowrap" }, children: label }) : null
      ]
    }
  );
}

// src/client/SpeakCard.tsx
var import_jsx_runtime2 = require("react/jsx-runtime");
function parse(block) {
  const b = block;
  const call = b && "kind" in b ? b.call : b;
  let text = "";
  try {
    const args = JSON.parse(call?.argsRaw ?? "{}");
    text = typeof args.text === "string" ? args.text : "";
  } catch {
  }
  const settled = !!b && "kind" in b;
  const isError = settled && b.isError === true;
  let result = "";
  if (settled) {
    const content = b.content;
    if (content?.length === 1 && content[0]?.type === "text") result = content[0].text ?? "";
  }
  return { text, state: !settled ? "running" : isError ? "error" : "done", result };
}
function SpeakCard({ block }) {
  const { text, state, result } = parse(block);
  const color = state === "running" ? "var(--dsw-alias-label-tertiary, #888)" : state === "error" ? "var(--dsw-alias-state-error-primary, #e66)" : "var(--dsw-alias-label-secondary, #aaa)";
  const statusText = state === "running" ? "\u64AD\u62A5\u4E2D\u2026" : state === "error" ? `\u64AD\u62A5\u5931\u8D25${result ? `\uFF1A${result}` : ""}` : "\u5DF2\u64AD\u62A5";
  return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
    "div",
    {
      style: {
        display: "flex",
        alignItems: "baseline",
        gap: 6,
        minWidth: 0,
        margin: "4px 0 4px 4px",
        fontSize: 13,
        lineHeight: "24px",
        color
      },
      title: state === "error" ? result : void 0,
      children: [
        /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
          "svg",
          {
            width: "14",
            height: "14",
            viewBox: "0 0 24 24",
            fill: "none",
            stroke: "currentColor",
            strokeWidth: "2",
            strokeLinecap: "round",
            strokeLinejoin: "round",
            style: { flex: "none", alignSelf: "center" },
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("polygon", { points: "11 5 6 9 2 9 2 15 6 15 11 19 11 5" }),
              /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("path", { d: "M15.54 8.46a5 5 0 0 1 0 7.07" }),
              /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("path", { d: "M19.07 4.93a10 10 0 0 1 0 14.14" })
            ]
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { style: { flex: "none" }, children: "\u8BED\u97F3\u64AD\u62A5" }),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
          "span",
          {
            style: {
              flex: "auto",
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              color: "var(--dsw-alias-label-tertiary, #888)"
            },
            children: text
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { style: { flex: "none", fontSize: 11, opacity: 0.8 }, children: statusText })
      ]
    }
  );
}

// src/client/index.ts
var NS = "voice-input";
var inject = ["slots", "sessions", "workspaces", "uiWorkspace", "locale"];
function apply(ctx) {
  const c = ctx;
  ctx.effect(() => {
    let seen = -1;
    let pending = null;
    const timer = setInterval(() => {
      void (async () => {
        try {
          const resp = await fetch("/voice/sessions/active");
          const data = await resp.json();
          const revision = typeof data.revision === "number" ? data.revision : 0;
          if (seen < 0) {
            seen = revision;
            return;
          }
          if (revision !== seen) {
            seen = revision;
            pending = data.sessionId ?? null;
          }
          if (pending === null) return;
          const snap = c.sessions.list.getSnapshot();
          if (snap.byId[pending] === void 0) return;
          if (snap.current !== pending) c.uiWorkspace.openSession(pending);
          pending = null;
        } catch {
        }
      })();
    }, 2e3);
    return () => clearInterval(timer);
  });
  c.slots.inject(
    "sidebar.footer.action",
    () => c.slots.register(
      { name: "sidebar.footer.action", id: "voice-input", order: 200, locale: NS },
      VoiceButton
    )
  );
  c.slots.inject(
    "tool.call.toolview",
    () => c.slots.register(
      { name: "tool.call.toolview", key: "mcp__voice__speak", locale: NS },
      SpeakCard
    )
  );
}
return module.exports; } });

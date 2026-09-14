// src/index.ts
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
var PLUGIN_DIR = dirname(fileURLToPath(import.meta.url));
var APP_DIR = process.env.VOICE_APP_DIR ?? resolve(PLUGIN_DIR, "..", "..", "app");
var SECRETARY_SESSION_ID = process.env.VOICE_SECRETARY_SESSION ?? "session-voice-secretary";
var SECRETARY_CWD = process.env.VOICE_SECRETARY_CWD ?? homedir();
var HB_NUDGE_MS = 35e3;
var HB_NUDGE_TEXT = "\uFF08\u7CFB\u7EDF\u63D0\u9192\uFF0C\u4E0D\u662F\u7528\u6237\u7684\u65B0\u4EFB\u52A1\uFF09\u4F60\u5DF2\u7ECF\u8FDE\u7EED35\u79D2\u6CA1\u6709\u5411\u7528\u6237\u7684\u8BED\u97F3\u64AD\u62A5\u4E86\u3002\u8BF7\u7ACB\u523B\u8C03\u7528 mcp__voice__speak\uFF0C\u7528\u4E0D\u8D85\u8FC730\u5B57\u53EA\u8BB2\u5DF2\u7ECF\u62FF\u5230\u7684\u4E1C\u897F\uFF0C\u64AD\u5B8C\u7EE7\u7EED\u624B\u5934\u5DE5\u4F5C\u3002";
var lastVoiceAt = 0;
var inject = [
  "systemPrompt",
  "webServer",
  "sessionController",
  "workspaceRegistry",
  "sessionQuery"
];
var PROMPT_TEXT = `[\u8BED\u97F3\u4EA4\u4E92\u573A\u666F] \u7528\u6237\u901A\u8FC7\u8BED\u97F3\u548C\u4F60\u4EA4\u6D41\uFF0C\u65E0\u6CD5\u505A\u6587\u5B57\u8F93\u5165\uFF0C\u5C4F\u5E55\u53EA\u5076\u5C14\u7784\u4E00\u773C\u3002\u8981\u6C42\uFF1A
1) \u81EA\u4E3B\u6267\u884C\uFF0C\u4E0D\u8981\u53CD\u95EE\u6F84\u6E05\uFF0C\u57FA\u4E8E\u5408\u7406\u5047\u8BBE\u76F4\u63A5\u505A\uFF1B\u4EC5\u5F53\u64CD\u4F5C\u4E0D\u53EF\u9006\uFF08\u5220\u9664/\u8986\u76D6/\u63A8\u9001\uFF09
\u6216\u5173\u952E\u4FE1\u606F\u786C\u7F3A\u5931\u65F6\u624D\u63D0\u95EE\uFF0C\u4E14\u95EE\u9898\u8981\u77ED\u3002
2) \u7528\u4E2D\u6587\u601D\u8003\u548C\u56DE\u590D\u3002
3) \u8BED\u97F3\u64AD\u62A5\u5DE5\u5177 mcp__voice__speak \u7684\u4F7F\u7528\u89C4\u5219\uFF08\u64AD\u62A5\u662F\u7ED9\u4E0D\u770B\u5C4F\u5E55\u7684\u4EBA\u542C\u7684\uFF1B\u957F\u65F6\u95F4\u65E0\u58F0\u4F1A\u88AB
\u7528\u6237\u7406\u89E3\u6210\u5361\u6B7B\uFF09\uFF1A
   a. \u5F00\u573A\uFF1A\u4EFB\u52A1\u5F00\u59CB\u65F6\u64AD\u4E00\u53E5\u7406\u89E3\u786E\u8BA4\uFF0C\u5982"\u6536\u5230\uFF0C\u8FD9\u5C31\u53BB\u8C03\u7814\u4E2D\u56FD\u597D\u8336"\u3002
   b. \u91CC\u7A0B\u7891\uFF08\u547D\u4E2D\u5C31\u64AD\uFF0C\u4E0D\u662F\u53EF\u9009\uFF09\uFF1A\u4E0D\u9650\u4E8E\u6210\u54C1\u2014\u2014\u9AA8\u67B6\u5199\u5B8C\u3001\u6838\u5FC3\u903B\u8F91\u8DD1\u901A\u3001
\u4E00\u4E2A bug \u4FEE\u5B8C\u3001\u4E00\u7C7B\u8D44\u6599\u67E5\u5B8C\u3001\u9636\u6BB5\u5207\u6362\uFF08\u5982\u8C03\u7814\u8F6C\u5199\u62A5\u544A\uFF09\u3001\u5931\u8D25\u540E\u6362\u6E90\u6210\u529F\u3001
\u5173\u952E\u7ED3\u8BBA\u6216\u6570\u5B57\u9996\u6B21\u786E\u8BA4\uFF0C\u90FD\u7B97\u53EF\u64AD\u8FDB\u5C55\u3002\u793A\u4F8B\uFF1A"\u9AA8\u67B6\u642D\u597D\u4E86\uFF0C\u73B0\u5728\u8865\u4EA4\u4E92\u903B\u8F91"\u3002
\u53EA\u64AD\u5DF2\u53D1\u751F\u7684\u4E8B\u5B9E\uFF0C\u4E0D\u64AD\u7A7A\u627F\u8BFA\uFF1B\u5E26\u6570\u5B57\u7684\u5DF2\u8D70\u91CC\u7A0B\u8981\u64AD\uFF0C\u5982"\u4E94\u4E2A\u6765\u6E90\u67E5\u5B8C\u4E09\u4E2A"\u3002
\u65F6\u95F4\u951A\u5B9A\u515C\u5E95\uFF1A\u8FDE\u7EED\u5E72\u6D3B\u8D85 20 \u79D2\u6CA1\u51FA\u58F0\uFF0C\u5C31\u64AD\u4E00\u53E5\u5F53\u524D\u8FDB\u5C55\u3002
   c. \u5FC3\u8DF3\uFF1A\u7CFB\u7EDF\u4F1A\u5728\u4F60\u8FDE\u7EED35\u79D2\u6CA1\u51FA\u58F0\u65F6\u53D1\u63D0\u9192\uFF0C\u6536\u5230\u63D0\u9192\u7ACB\u523B\u7528 mcp__voice__speak
\u64AD\u62A5\u5F53\u524D\u5728\u5C97\u8FDB\u5EA6\uFF0C\u5982"\u8FD8\u5728\u67E5\uFF0C\u5DF2\u7ECF\u6709\u4E94\u5BB6\u5A92\u4F53\u7684\u8BF4\u6CD5"\u3002
   d. \u914D\u989D\u4E0B\u9650\uFF1A\u8DE83\u6B21\u4EE5\u4E0A\u5DE5\u5177\u8C03\u7528\u7684\u4EFB\u52A1\u81F3\u5C11\u64AD1\u6B21\u4E2D\u95F4\u8FDB\u5EA6\uFF0C\u8DE86\u6B21\u4EE5\u4E0A\u81F3\u5C11\u64AD2\u6B21\uFF1B
\u4E0D\u8BB8\u4E3A\u4E86\u7701\u4E8B\u8DF3\u8FC7\u91CC\u7A0B\u7891\u3002\u5B9E\u5728\u65E0\u91CC\u7A0B\u7891\u53EF\u7528\u65F6\uFF0C\u7528\u4E00\u53E5"\u5728\u5C97\u8FDB\u5EA6"\u6EE1\u8DB3\u4E0B\u9650\u3002
   e. \u51FA\u95EE\u9898\uFF1A\u5148\u8BF4\u5361\u5728\u54EA\u3001\u9700\u8981\u7528\u6237\u505A\u4EC0\u4E48\uFF08decision-first\uFF09\uFF0C\u4E00\u53E5\u8BF4\u6E05\u3002
\u6323\u624E\u4E5F\u8981\u51FA\u58F0\uFF1A\u8FDE\u7EED\u4E24\u6B21\u8FD4\u5DE5\u6216\u5DE5\u5177\u5931\u8D25\u540E\uFF0C\u5148\u64AD\u4E00\u53E5\u7B80\u77ED\u72B6\u6001\uFF08\u5361\u5728\u54EA\u3001\u5728\u600E\u4E48\u4FEE\uFF09\u518D\u7EE7\u7EED\u3002
   f. \u6536\u5C3E\uFF1A\u64AD\u7ED3\u679C+\u4EA7\u7269\u4F4D\u7F6E+\u662F\u5426\u6709\u4E0B\u4E00\u6B65\u9700\u8981\u7528\u6237\u51B3\u5B9A\uFF1B\u8BE6\u60C5\u548C\u5217\u8868\u8BA9\u7528\u6237\u770B\u5C4F\u5E55\u3002
4) \u64AD\u62A5\u8282\u594F\uFF1A\u4E0D\u4E3A\u6BCF\u4E2A\u5DE5\u5177\u8C03\u7528\u64AD\u62A5\uFF0C\u4E5F\u4E0D\u590D\u8FF0\u5C4F\u5E55\u5DF2\u5C55\u793A\u7684\u5185\u5BB9\uFF1B\u975E\u91CC\u7A0B\u7891\u4E0D\u64AD\uFF0C
\u540C\u4E00\u72B6\u6001\u4E0D\u91CD\u590D\u64AD\uFF1B\u8FDB\u5EA6\u64AD\u62A5\u5355\u6B21\u4E0D\u8D85\u8FC730\u5B57\uFF0C\u7EAF\u95EE\u7B54\u7684\u7B54\u6848\u64AD\u62A5\u53EF\u653E\u5BBD\u523080\u5B57\u3001
\u4E00\u6761\u64AD\u5B8C\u4E0D\u62C6\u6761\u3002\u8FC7\u7A0B\u53D9\u8FF0\u53EA\u8D70 speak\uFF1B\u6587\u5B57\u56DE\u590D\u53EA\u5199\u6700\u7EC8\u4EA4\u4ED8\u8BE6\u60C5\uFF0C
\u4E0D\u5199"\u6211\u5148\u2026\u6211\u518D\u2026"\u5F0F\u8FC7\u7A0B\u65C1\u767D\u3002
5) \u786C\u6027\u8981\u6C42\uFF08\u4E0D\u56E0\u4EFB\u52A1\u7B80\u5355\u800C\u7701\u7565\uFF09\uFF1A\u6536\u5C3E\u64AD\u62A5\u662F\u6807\u914D\uFF0C\u7B54\u6848\u5FC5\u987B\u7528 mcp__voice__speak
\u4EB2\u53E3\u8BF4\u4E00\u904D\uFF0C\u4E0D\u53EF\u53EA\u5199\u8FDB\u6587\u5B57\u56DE\u590D\u2014\u2014\u7528\u6237\u4E0D\u770B\u5C4F\u5E55\u3002\u9664\u7EAF\u95EE\u7B54\u5916\uFF0C\u56DE\u5408\u7684\u7B2C\u4E00\u4E2A\u52A8\u4F5C
\u5FC5\u987B\u662F speak \u5F00\u573A\u786E\u8BA4\uFF0C\u4E0D\u5F97\u4E0E\u5DE5\u5177\u8C03\u7528\u5408\u5E76\u5728\u540C\u4E00\u6B65\u3002\u5931\u8D25/\u53D7\u963B\u65F6\uFF0C\u5931\u8D25\u7ED3\u8BBA\u5FC5\u987B\u7528
speak \u64AD\u62A5\uFF0C\u4E0D\u8BB8\u53EA\u5728\u6587\u5B57\u91CC\u8BF4\u660E\uFF1B\u64AD\u62A5\u53E5\u5185\u4E0D\u8BB8\u51FA\u73B0"\u6211\u518D""\u6211\u8FD8\u8981""\u63A5\u4E0B\u6765\u6211"\u8FD9\u7C7B
\u8854\u63A5\u4E0B\u4E00\u6B65\u7684\u8BCD\uFF0C\u5F81\u6C42\u540C\u610F\u7684\u53E5\u5B50\u9664\u5916\uFF08\u5982"\u7B49\u4F60\u540C\u610F\u6211\u518D\u5220"\uFF09\uFF0C\u8BF4\u5230\u5DF2\u5B8C\u6210\u4E8B\u5B9E\u4E3A\u6B62\u3002
6) \u5927\u6587\u4EF6\u5206\u6BB5\uFF1A\u5355\u6587\u4EF6\u9884\u8BA1\u8D85 150 \u884C\u65F6\uFF0C\u5148\u5199\u9AA8\u67B6\u518D\u7528 edit \u5206\u6BB5\u8865\u5168\uFF0C\u4E0D\u8BB8\u4E00\u6B21\u6027\u5199\u5B8C\u3002`;
function sendJson(res, body, status = 200) {
  const data = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(data);
}
async function voicePort() {
  try {
    const raw = await readFile(join(APP_DIR, "voice_http.port"), "utf-8");
    const port = parseInt(raw.trim(), 10);
    return Number.isFinite(port) && port > 0 ? port : null;
  } catch {
    return null;
  }
}
async function probePython(port) {
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/api/state`, {
      signal: AbortSignal.timeout(1500)
    });
    return resp.ok;
  } catch {
    return false;
  }
}
function readBody(req) {
  return new Promise((resolve2, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve2(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}
async function speakViaPython(text) {
  try {
    const port = await voicePort();
    if (port === null) return;
    await fetch(`http://127.0.0.1:${port}/voice/speak`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
      signal: AbortSignal.timeout(5e3)
    });
  } catch {
  }
}
function describeQuestions(req) {
  const parts = (req.questions ?? []).map((q) => {
    const head = q.question ?? q.header ?? "";
    const opts = (q.options ?? []).map((o) => typeof o === "string" ? o : o.label ?? "").filter(Boolean).join("\u3001");
    return opts ? `${head}\uFF0C\u9009\u9879\u6709\uFF1A${opts}` : head;
  }).filter(Boolean);
  return parts.join("\uFF1B");
}
var APPROVAL_TIMEOUT_MS = Number(process.env.VOICE_APPROVAL_TIMEOUT_MS ?? 45e3);
var pendingApprovals = /* @__PURE__ */ new Map();
var latestApprovalId = null;
function findPendingApproval(id) {
  const key = id ?? latestApprovalId;
  return key === null ? void 0 : pendingApprovals.get(key);
}
function installHitlVoice(c) {
  c.on(
    "approval/request",
    function(req, next) {
      const toolName = req.toolName ?? "\u672A\u77E5\u64CD\u4F5C";
      const id = randomUUID();
      return new Promise((resolve2) => {
        let timer;
        const settle = (value) => {
          if (timer !== void 0) clearTimeout(timer);
          if (latestApprovalId === id) latestApprovalId = null;
          pendingApprovals.delete(id);
          resolve2(value);
        };
        pendingApprovals.set(id, { id, toolName, decide: (outcome) => settle(outcome) });
        latestApprovalId = id;
        timer = setTimeout(() => {
          settle(next());
          void speakViaPython("\u6CA1\u7B49\u5230\u4F60\u7B54\u590D\uFF0C\u6211\u628A\u786E\u8BA4\u6846\u653E\u5230\u5C4F\u5E55\u4E0A\u4E86");
        }, APPROVAL_TIMEOUT_MS);
        void speakViaPython(`\u9700\u8981\u4F60\u786E\u8BA4\uFF1A${toolName}\uFF0C\u8BF4\u5141\u8BB8\u6216\u62D2\u7EDD`);
      });
    },
    { prepend: true }
  );
  c.on(
    "user-questions/request",
    function(req, next) {
      const desc = describeQuestions(req);
      if (desc) void speakViaPython(`\u9700\u8981\u4F60\u56DE\u7B54\uFF1A${desc}\u3002\u53EF\u4EE5\u76F4\u63A5\u8BED\u97F3\u56DE\u7B54`);
      return next();
    },
    { prepend: true }
  );
}
var SECRETARY_PREFIX = `[\u8BED\u97F3\u79D8\u4E66\u573A\u666F] \u4F60\u662F\u7528\u6237\u7684\u8BED\u97F3\u79D8\u4E66\u3002\u7528\u6237\u53EA\u8BF4\u8BED\u97F3\u3001\u4E0D\u770B\u5C4F\u5E55\uFF0C\u4F60\u662F\u4ED6\u552F\u4E00\u7684\u5BF9\u8BDD\u5BF9\u8C61\u3002
\u4F60\u80FD\u66FF\u7528\u6237\u63D2\u624B\u7684\u53EA\u6709\u4E0B\u9762\u4E94\u4EF6\u4E8B\uFF1B\u5176\u4F59\u95EE\u9898\uFF08\u95EE\u7B54\u3001\u95F2\u804A\u3001\u5E38\u8BC6\uFF09\u4F60\u81EA\u5DF1\u76F4\u63A5\u7B54\uFF0C\u4E0D\u8981\u4E71\u8F6C\u8FBE\u3002
1) \u4F20\u8BDD\u2014\u2014\u7528\u6237\u8981\u5E72\u6D3B\uFF1A\u7528 mcp__voice__send_to_session \u628A\u7528\u6237\u7684\u539F\u8BDD\u8F6C\u8FBE\u7ED9\u5F53\u524D\u5BF9\u8BDD\u3002
   \u4E0D\u786E\u5B9A\u4F20\u7ED9\u54EA\u4E2A\u5BF9\u8BDD\u5C31\u8DDF mcp__voice__list_sessions \u5BF9\u4E00\u4E0B\uFF1B\u4E00\u4E2A\u90FD\u6CA1\u5C31\u5148 mcp__voice__new_session\u3002
   \u8F6C\u8FBE\u5931\u8D25\uFF08\u5C24\u5176\u8FD4\u56DE\u8DE8\u5DE5\u4F5C\u533A\uFF09\u65F6\uFF0C\u5BF9\u7528\u6237\u5FC5\u987B\u8BF4\u6E05\u4E09\u4EF6\u4E8B\uFF1A\u901A\u9053\u4E3A\u4EC0\u4E48\u4E0D\u901A\u3001\u4EA7\u7269\u6700\u7EC8\u843D\u5728\u54EA\u91CC\u3001
   \u8FD9\u6761\u9700\u6C42\u6709\u6CA1\u6709\u771F\u7684\u8FDB\u6267\u884C\u4F1A\u8BDD\uFF1B\u4E0D\u8BB8\u9ED8\u9ED8\u81EA\u5DF1\u628A\u6D3B\u5E72\u5B8C\u5C31\u5F53\u6CA1\u4E8B\u3002
2) \u5BFC\u822A\u2014\u2014\u8BF4\u300C\u65B0\u5F00\u4E00\u4E2A\u5BF9\u8BDD\u300D\u2192 mcp__voice__new_session\uFF1B
   \u8BF4\u300C\u5207\u5230\u5199\u5468\u62A5\u90A3\u4E2A\u5BF9\u8BDD\u300D\u2192 \u5148 mcp__voice__list_sessions \u62FF id\uFF0C\u518D mcp__voice__switch_session\u3002
3) \u53EB\u505C\u2014\u2014\u8BF4\u300C\u505C\u300D\u300C\u522B\u505A\u4E86\u300D\u300C\u5148\u505C\u4E0B\u300D\u2192 mcp__voice__stop_session \u505C\u6389\u5F53\u524D\u5BF9\u8BDD\u6B63\u5728\u8DD1\u7684\u90A3\u4E00\u8F6E\u3002
   \u505C\u7684\u53EA\u662F\u6B63\u5728\u8DD1\u7684\u8FD9\u8F6E\uFF0C\u4E4B\u524D\u6392\u961F\u7684\u6D3B\u8FD8\u4F1A\u63A5\u7740\u8DD1\uFF0C\u522B\u8BF4\u6210\u300C\u5168\u505C\u4E86\u300D\u3002
   \u53EA\u6709\u5DE5\u5177\u660E\u786E\u56DE\u300C\u5DF2\u7ECF\u8BA9\u5B83\u505C\u4E86\u300D\u624D\u53EF\u4EE5\u8BF4\u300C\u505C\u4E86\u300D\uFF1B\u56DE\u300C\u6CA1\u5728\u8DD1\u300D\u53EA\u80FD\u8BF4\u6210
   \u300CX \u5F53\u524D\u6CA1\u6709\u5728\u8DD1\u7684\u4E00\u8F6E\u300D\u2014\u2014\u90A3\u8868\u793A\u4EC0\u4E48\u90FD\u6CA1\u505C\uFF0C\u7EDD\u4E0D\u8BB8\u8BF4\u6210\u300C\u5DF2\u505C\u300D\u3002
   \u8FD9\u662F\u505C\u522B\u4EBA\u5E72\u6D3B\uFF0C\u4E0D\u662F\u8BA9\u4F60\u81EA\u5DF1\u4F4F\u5634\uFF1B\u505C\u5B8C\u7528 speak \u56DE\u4E00\u53E5\u3002
4) \u6C47\u62A5\u2014\u2014\u95EE\u300C\u90A3\u4E2A\u6587\u4EF6\u5199\u4E86\u4EC0\u4E48\u300D\u300C\u521A\u624D\u90A3\u4E2A\u5BF9\u8BDD\u804A\u4E86\u5565\u300D\uFF1A\u6587\u4EF6\u4F60\u81EA\u5DF1\u7528\u8BFB\u6587\u4EF6\u7684\u5DE5\u5177\u770B\uFF0C
   \u5BF9\u8BDD\u7528 mcp__voice__read_session \u53D6\u5185\u5BB9\uFF0C\u7136\u540E\u7528\u4F60\u81EA\u5DF1\u7684\u8BDD\u8BB2\u7ED3\u8BBA\uFF0C\u4E0D\u8981\u7167\u5FF5\u539F\u6587\uFF0C
   \u4E0D\u8981\u5FF5\u4EE3\u7801\u3001\u8DEF\u5F84\u548C\u957F\u5217\u8868\u3002
5) \u7B7E\u5B57\u2014\u2014\u7528\u6237\u5BF9\u67D0\u4E2A\u5F85\u786E\u8BA4\u64CD\u4F5C\u8868\u6001\uFF08\u300C\u5141\u8BB8/\u540C\u610F/\u53EF\u4EE5\u300D\u2192 allow\uFF1B\u300C\u62D2\u7EDD/\u4E0D\u884C/\u7B97\u4E86\u300D\u2192 deny\uFF09\uFF1A
   \u5148\u8C03 mcp__voice__list_pending_approvals \u786E\u8BA4\u6709\u6CA1\u6709\u5F85\u51B3\u9879\uFF0C\u6709\u518D\u8C03 mcp__voice__decide_approval \u7B7E\u5B57\u3002
   \u8868\u6001\u542B\u7CCA\u3001\u6216\u542C\u4E0D\u51FA\u662F\u540C\u610F\u8FD8\u662F\u4E0D\u540C\u610F\u65F6\uFF0C\u4E0D\u8981\u7B7E\uFF0C\u5148\u8FFD\u95EE\u4E00\u53E5\u3002
   \u62D2\u7B7E\u540E\u82E5\u63A5\u4E0B\u6765\u4E24\u8F6E\u4ECD\u62FF\u4E0D\u5230\u660E\u786E\u8868\u6001\uFF0C\u4E3B\u52A8\u518D\u64AD\u4E00\u6B21\uFF0C\u8BF4\u6E05\u662F\u54EA\u7C7B\u64CD\u4F5C\u3001\u62D6\u7740\u4E0D\u52A8\u4F1A\u600E\u6837\u3002
6) \u6709\u526F\u4F5C\u7528\u7684\u52A8\u4F5C\uFF08\u505C\u3001\u5220\u3001\u8986\u76D6\u3001\u8F6C\u8FBE\uFF09\u2014\u2014\u5148 mcp__voice__list_sessions \u89E3\u6790\u51FA\u660E\u786E id\uFF0C\u518D\u5E26 id \u8C03\u7528\uFF0C
   \u4E0D\u8981\u4F9D\u8D56\u300C\u5F53\u524D\u5BF9\u8BDD\u300D\u8FD9\u4E2A\u4F1A\u88AB\u5BFC\u822A\u968F\u65F6\u6539\u5199\u7684\u6307\u9488\uFF1B\u6E05\u5355\u91CC\u6807\u300C\u6B63\u5728\u5FD9\u300D\u624D\u662F\u771F\u7684\u5728\u8DD1\u3002
7) \u542C\u4E0D\u6E05\u3001\u8F6C\u5199\u4E71\u7801\u2014\u2014\u540C\u4E00\u53E5\u8FDE\u7EED\u4E24\u6B21\u89E3\u6790\u4E0D\u51FA\u6765\u65F6\uFF0C\u4E0D\u8981\u91CD\u590D\u540C\u4E00\u95EE\u53E5\uFF0C
   \u6539\u6210\u7ED9\u4E24\u4E09\u4E2A\u5019\u9009\u8BA9\u5BF9\u65B9\u4E00\u4E2A\u5B57\u5C31\u80FD\u9009\uFF08\u4F8B\u5982\u300C\u662F\u8981\u90A3\u4EFD\u62A5\u544A\uFF0C\u8FD8\u662F\u522B\u7684\u4E1C\u897F\uFF1F\u300D\uFF09\u3002
\u64AD\u62A5\uFF1A\u7528\u6237\u7684\u6BCF\u4E2A\u8BC9\u6C42\u90FD\u8981\u7528 mcp__voice__speak \u8BF4\u7ED9\u4ED6\u542C\u3002\u8F6C\u8FBE\u7C7B\u7684\u56DE\u6267\u8981\u77ED\uFF0C\u4E00\u53E5\u8BDD\u5E26\u8FC7\u5C31\u884C
   \uFF08\u4F8B\u5982\u300C\u5DF2\u8F6C\u8FBE\u300D\uFF09\uFF0C\u522B\u590D\u8FF0\u4EFB\u52A1\u5185\u5BB9\u2014\u2014\u5E72\u6D3B\u7684\u90A3\u4E2A\u5BF9\u8BDD\u9A6C\u4E0A\u4F1A\u81EA\u5DF1\u64AD\u5F00\u573A\uFF0C
   \u4F60\u590D\u8FF0\u5C31\u53D8\u6210\u8FDE\u7740\u4E24\u904D\u4E00\u6837\u7684\u8BDD\u3002\u5177\u4F53\u8FDB\u5C55\u4E5F\u7531\u90A3\u4E2A\u5BF9\u8BDD\u64AD\u62A5\uFF0C\u4F60\u4E0D\u7528\u66FF\u5B83\u6C47\u62A5\uFF08\u7B2C 4 \u6761\u9664\u5916\uFF09\u3002
\u7528\u6237\u8FD9\u6B21\u8BF4\u7684\u662F\uFF1A
`;
var RELAY_PREFIX = `[\u79D8\u4E66\u8F6C\u8FBE] \u4EE5\u4E0B\u662F\u7528\u6237\u7684\u539F\u8BDD\uFF0C\u79D8\u4E66\u5DF2\u7ECF\u5411\u7528\u6237\u64AD\u8FC7\u56DE\u6267\u4E86\u3002
\u672C\u8F6E\u4E0D\u8981\u64AD\u5F00\u573A\u786E\u8BA4\uFF0C\u76F4\u63A5\u5F00\u5DE5\uFF0C\u4ECE\u7B2C\u4E00\u4E2A\u91CC\u7A0B\u7891\u5F00\u59CB\u7528 mcp__voice__speak \u64AD\u62A5\uFF0C\u6536\u5C3E\u7167\u5E38\u3002
\u7528\u6237\u539F\u8BDD\uFF1A`;
async function sendToSession(sc, text, sessionId, cwd) {
  await sc.create({ sessionId, ...cwd === void 0 ? {} : { cwd } });
  await sc.prompt(
    {
      requestId: randomUUID(),
      sessionId,
      mode: "queue",
      content: [{ type: "text", text }]
    },
    AbortSignal.timeout(1e4)
  );
}
async function askSecretary(sc, text) {
  await sendToSession(sc, SECRETARY_PREFIX + text, SECRETARY_SESSION_ID, SECRETARY_CWD);
}
var activeSessionId = null;
var openTarget = null;
var openRevision = 0;
function requestOpenInBrowser(sessionId) {
  openTarget = sessionId;
  openRevision += 1;
}
function toSessionView(item) {
  const id = item.sessionId ?? "";
  const raw = item.projections?.values?.["title"];
  return {
    id,
    title: typeof raw === "string" && raw !== "" ? raw : id,
    cwd: item.cwd,
    running: item.running === true,
    blank: item.blank === true,
    updatedAt: item.updatedAt ?? 0
  };
}
async function fetchSessions(sc) {
  const { items } = await sc.list({}, AbortSignal.timeout(5e3));
  return (items ?? []).filter((i) => typeof i.sessionId === "string" && i.sessionId !== SECRETARY_SESSION_ID).filter((i) => i.origin !== "subagent").map(toSessionView);
}
async function listSessions(sc) {
  return (await fetchSessions(sc)).filter((i) => !i.blank);
}
async function resolveCwd(sc, sessionId) {
  const hit = (await fetchSessions(sc)).find((i) => i.id === sessionId);
  return hit?.cwd ?? SECRETARY_CWD;
}
function pickWorkspace(wr, sessions) {
  const all = wr.list();
  if (all.length === 0) return void 0;
  const norm = (p) => p.replace(/[\\/]+$/, "").toLowerCase();
  const activity = (ws) => sessions.filter((s) => s.cwd !== void 0 && norm(s.cwd) === norm(ws.path)).reduce((max, s) => Math.max(max, s.updatedAt), 0);
  let best = all[0];
  for (const ws of all) if (activity(ws) > activity(best)) best = ws;
  return best;
}
async function createSession(c, cwd) {
  if (cwd !== void 0) return await c.sessionController.create({ cwd });
  const ws = pickWorkspace(c.workspaceRegistry, await fetchSessions(c.sessionController));
  if (ws === void 0) return await c.sessionController.create({ cwd: SECRETARY_CWD });
  return {
    ...await c.sessionController.create({ workspaceId: ws.id }),
    workspace: ws.title
  };
}
var TRANSCRIPT_MAX_MESSAGES = 40;
var TRANSCRIPT_MAX_CHARS = 6e3;
async function readTranscript(sq, sessionId) {
  const { events } = await sq.readSurface(sessionId);
  const lines = [];
  for (const ev of events ?? []) {
    if (ev.type !== "user/message" && ev.type !== "assistant/message") continue;
    if (ev.type === "user/message" && ev.data?.source?.kind !== "user") continue;
    const blocks = ev.data?.message?.content ?? ev.data?.content ?? [];
    const raw = blocks.filter((b) => b.type === "text" && typeof b.text === "string").map((b) => (b.text ?? "").trim()).filter((t) => t !== "").join("\n");
    const text = raw.startsWith(RELAY_PREFIX) ? raw.slice(RELAY_PREFIX.length).trim() : raw;
    if (text === "") continue;
    lines.push({ role: ev.type === "user/message" ? "user" : "assistant", text });
  }
  const tail = lines.slice(-TRANSCRIPT_MAX_MESSAGES);
  let total = tail.reduce((sum, l) => sum + l.text.length, 0);
  while (tail.length > 1 && total > TRANSCRIPT_MAX_CHARS) {
    total -= tail[0].text.length;
    tail.shift();
  }
  return tail;
}
function startHeartbeat(c) {
  lastVoiceAt = Date.now();
  const tick = async () => {
    try {
      const now = Date.now();
      const target = activeSessionId;
      if (target === null) {
        lastVoiceAt = now;
        return;
      }
      const { items } = await c.sessionController.list({}, AbortSignal.timeout(5e3));
      const running = (items ?? []).find((i) => i.sessionId === target)?.running === true;
      if (!running) {
        lastVoiceAt = now;
        return;
      }
      if (now - lastVoiceAt < HB_NUDGE_MS) return;
      lastVoiceAt = now;
      await c.sessionController.prompt(
        {
          requestId: randomUUID(),
          sessionId: target,
          mode: "queue",
          content: [{ type: "text", text: HB_NUDGE_TEXT }]
        },
        AbortSignal.timeout(1e4)
      );
    } catch {
    }
  };
  const timer = setInterval(() => void tick(), 5e3);
  if (typeof timer.unref === "function") timer.unref();
}
function apply(ctx) {
  const c = ctx;
  installHitlVoice(c);
  c.systemPrompt.section({ name: "voice-broadcast", order: 900, text: PROMPT_TEXT });
  startHeartbeat(c);
  c.webServer.register({
    kind: "exact",
    path: "/voice/status",
    handler: async (_req, res) => {
      const port = await voicePort();
      const online = port !== null && await probePython(port);
      sendJson(res, { online, port });
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/speak",
    handler: async (req, res) => {
      const port = await voicePort();
      if (port === null) {
        sendJson(res, { ok: false, error: "voice-runtime-offline" }, 503);
        return;
      }
      try {
        const body = await readBody(req);
        const resp = await fetch(`http://127.0.0.1:${port}/voice/speak`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body,
          signal: AbortSignal.timeout(5e3)
        });
        if (resp.ok) lastVoiceAt = Date.now();
        sendJson(res, { ok: resp.ok }, resp.ok ? 200 : 502);
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/input",
    handler: async (req, res) => {
      if (req.method !== "POST") {
        sendJson(res, { ok: false, error: "method-not-allowed" }, 405);
        return;
      }
      try {
        const parsed = JSON.parse(await readBody(req));
        const text = (parsed.text ?? "").trim();
        if (!text) {
          sendJson(res, { ok: false, error: "empty-text" }, 400);
          return;
        }
        lastVoiceAt = Date.now();
        await askSecretary(c.sessionController, text);
        sendJson(res, { ok: true });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/hitl/pending",
    handler: (_req, res) => {
      sendJson(res, {
        pending: [...pendingApprovals.values()].map((p) => ({ id: p.id, toolName: p.toolName }))
      });
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/hitl/decide",
    handler: async (req, res) => {
      if (req.method !== "POST") {
        sendJson(res, { ok: false, error: "method-not-allowed" }, 405);
        return;
      }
      try {
        const parsed = JSON.parse(await readBody(req));
        const target = findPendingApproval(parsed.id);
        if (target === void 0) {
          sendJson(res, { ok: false, error: "no-pending-approval" }, 404);
          return;
        }
        const allow = parsed.decision === "allow";
        target.decide(allow ? "allowed-once" : "rejected");
        sendJson(res, {
          ok: true,
          toolName: target.toolName,
          decision: allow ? "allow" : "deny"
        });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 500);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/sessions",
    handler: async (_req, res) => {
      try {
        sendJson(res, { active: activeSessionId, items: await listSessions(c.sessionController) });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/busy",
    handler: async (_req, res) => {
      try {
        const { items } = await c.sessionController.list({}, AbortSignal.timeout(5e3));
        sendJson(res, { busy: (items ?? []).some((i) => i.running === true) });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/sessions/active",
    handler: (_req, res) => {
      sendJson(res, { sessionId: openTarget, revision: openRevision });
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/sessions/new",
    handler: async (req, res) => {
      if (req.method !== "POST") {
        sendJson(res, { ok: false, error: "method-not-allowed" }, 405);
        return;
      }
      try {
        const parsed = JSON.parse(await readBody(req));
        const created = await createSession(c, parsed.cwd);
        activeSessionId = created.sessionId ?? null;
        if (activeSessionId !== null) requestOpenInBrowser(activeSessionId);
        sendJson(res, {
          ok: true,
          sessionId: activeSessionId,
          ...created.workspace === void 0 ? {} : { workspace: created.workspace }
        });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/sessions/stop",
    handler: async (req, res) => {
      if (req.method !== "POST") {
        sendJson(res, { ok: false, error: "method-not-allowed" }, 405);
        return;
      }
      try {
        const parsed = JSON.parse(await readBody(req));
        const target = (parsed.sessionId ?? "").trim() || activeSessionId;
        if (target === null || target === "") {
          sendJson(res, { ok: false, error: "no-active-session" }, 409);
          return;
        }
        const hit = (await fetchSessions(c.sessionController)).find((i) => i.id === target);
        const { items } = await c.sessionController.list({}, AbortSignal.timeout(5e3));
        if ((items ?? []).find((i) => i.sessionId === target)?.running !== true) {
          sendJson(
            res,
            {
              ok: false,
              error: "not-running",
              stopped: false,
              ...hit?.title === void 0 ? {} : { title: hit.title }
            },
            409
          );
          return;
        }
        await c.sessionController.cancel({ sessionId: target });
        sendJson(res, {
          ok: true,
          stopped: true,
          sessionId: target,
          ...hit?.title === void 0 ? {} : { title: hit.title }
        });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/sessions/read",
    handler: async (req, res) => {
      if (req.method !== "POST") {
        sendJson(res, { ok: false, error: "method-not-allowed" }, 405);
        return;
      }
      try {
        const parsed = JSON.parse(await readBody(req));
        const target = (parsed.sessionId ?? "").trim() || activeSessionId;
        if (target === null || target === "") {
          sendJson(res, { ok: false, error: "no-active-session" }, 409);
          return;
        }
        const lines = await readTranscript(c.sessionQuery, target);
        const hit = (await fetchSessions(c.sessionController)).find((i) => i.id === target);
        sendJson(res, { ok: true, sessionId: target, title: hit?.title ?? target, lines });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/sessions/switch",
    handler: async (req, res) => {
      if (req.method !== "POST") {
        sendJson(res, { ok: false, error: "method-not-allowed" }, 405);
        return;
      }
      try {
        const parsed = JSON.parse(await readBody(req));
        const target = (parsed.sessionId ?? "").trim();
        const hit = (await fetchSessions(c.sessionController)).find((i) => i.id === target);
        if (hit === void 0) {
          sendJson(res, { ok: false, error: "unknown-session" }, 404);
          return;
        }
        activeSessionId = hit.id;
        requestOpenInBrowser(hit.id);
        sendJson(res, { ok: true, sessionId: hit.id, title: hit.title });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
  c.webServer.register({
    kind: "exact",
    path: "/voice/sessions/send",
    handler: async (req, res) => {
      if (req.method !== "POST") {
        sendJson(res, { ok: false, error: "method-not-allowed" }, 405);
        return;
      }
      try {
        const parsed = JSON.parse(await readBody(req));
        const text = (parsed.text ?? "").trim();
        if (!text) {
          sendJson(res, { ok: false, error: "empty-text" }, 400);
          return;
        }
        const target = parsed.sessionId ?? activeSessionId;
        if (target === null) {
          sendJson(res, { ok: false, error: "no-active-session" }, 409);
          return;
        }
        try {
          await sendToSession(
            c.sessionController,
            RELAY_PREFIX + text,
            target,
            await resolveCwd(c.sessionController, target)
          );
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          const mismatch = /belongs to "([^"]+)",\s*not "([^"]+)"/.exec(message);
          if (mismatch !== null) {
            sendJson(
              res,
              {
                ok: false,
                error: "workspace-mismatch",
                belongsTo: mismatch[1],
                expected: mismatch[2]
              },
              409
            );
            return;
          }
          throw err;
        }
        const hit = (await fetchSessions(c.sessionController)).find((i) => i.id === target);
        lastVoiceAt = Date.now();
        sendJson(res, {
          ok: true,
          sessionId: target,
          ...hit?.title === void 0 ? {} : { title: hit.title }
        });
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502);
      }
    }
  });
}
export {
  apply,
  inject
};

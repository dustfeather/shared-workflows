// Workload identity federation: GitHub OIDC token -> Anthropic access token.
// Also resolves the newest model id per family, so no caller pins a version.
import { appendFileSync } from "node:fs";

const API = "https://api.anthropic.com";
const fail = (msg) => {
  console.log(`::error::${msg}`);
  process.exit(1);
};

// JavaScript actions receive inputs as INPUT_<NAME>, name upper-cased, hyphens kept.
const input = (name) => {
  const v = process.env[`INPUT_${name.toUpperCase()}`]?.trim();
  if (!v) fail(`input ${name} is empty: set the ANTHROPIC_* repository or organization variables`);
  return v;
};
const rule = input("federation-rule-id");
const org = input("organization-id");
const svac = input("service-account-id");
const ws = input("workspace-id");

const reqUrl = process.env.ACTIONS_ID_TOKEN_REQUEST_URL;
const reqTok = process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN;
if (!reqUrl || !reqTok) fail("no GitHub OIDC endpoint: the job needs `permissions: id-token: write`");

const oidc = await fetch(`${reqUrl}&audience=${encodeURIComponent(API)}`, {
  headers: { authorization: `bearer ${reqTok}` },
});
const jwt = (await oidc.json().catch(() => ({}))).value;
if (!jwt) fail(`GitHub OIDC token request failed (${oidc.status})`);

const exchange = await fetch(`${API}/v1/oauth/token`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
    assertion: jwt,
    federation_rule_id: rule,
    organization_id: org,
    service_account_id: svac,
    workspace_id: ws,
  }),
});
const grant = await exchange.json().catch(() => ({}));
// The error body names the reason (e.g. match_subject_prefix) and carries no secret.
if (!grant.access_token) fail(`token exchange failed (${exchange.status}): ${JSON.stringify(grant.error ?? grant)}`);
const token = grant.access_token;
console.log(`::add-mask::${token}`);

const headers = { authorization: `Bearer ${token}`, "anthropic-version": "2023-06-01" };
const models = [];
for (let after = ""; ; ) {
  const r = await fetch(`${API}/v1/models?limit=1000${after ? `&after_id=${after}` : ""}`, { headers });
  const page = await r.json().catch(() => ({}));
  if (!r.ok) fail(`model list failed (${r.status}): ${JSON.stringify(page.error ?? page)}`);
  models.push(...(page.data ?? []));
  if (!page.has_more || !page.last_id) break;
  after = page.last_id;
}

const newest = (family) =>
  models
    .filter((m) => m.id.startsWith(`claude-${family}-`))
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0]?.id ?? "";

const out = { token, "model-opus": newest("opus"), "model-sonnet": newest("sonnet"), "model-haiku": newest("haiku") };
// Only a missing token is fatal here: a family absent from /v1/models leaves its
// output empty, and the caller that needs that family fails on it, not every caller.
for (const [k, v] of Object.entries(out)) if (!v && k !== "token") console.log(`::warning::no model found for ${k}`);
appendFileSync(process.env.GITHUB_OUTPUT, Object.entries(out).map(([k, v]) => `${k}=${v}\n`).join(""));
console.log(`token expires in ${grant.expires_in}s; models: ${out["model-opus"]}, ${out["model-sonnet"]}, ${out["model-haiku"]}`);

#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: pulled-play-inventory
 * description: Lists every Play package installed on this machine with its version, the date it was pulled, its step count and its size on disk. Read-only, python3 standard library only, no network and no credentials.
 * provenance:
 *   author: sookra <stephensookra@gmail.com>
 * metadata:
 *   version: 0.1.0
 *   rote_version: 0.78.0
 *   status: released
 *   kind: atomic
 *   flow_type: parallel
 *   execution_model: steps_with_presentation
 *   requires_sessions: false
 * parameters:
 * - name: flows_root
 *   type: string
 *   required: false
 *   default: ~/.rote/flows
 *   description: Directory holding the installed packages. Change it to inspect a rote store other than the default one.
 * - name: owner
 *   type: string
 *   required: false
 *   default: ''
 *   description: Restrict the inventory to a single publisher namespace, for example modiqo. Empty lists every publisher.
 * steps:
 *   pull_provenance:
 *     type: process.exec
 *     timeout_ms: 20000
 *     argv:
 *     - python3
 *     - '@resource{_rote_python_c.py}'
 *     - '@resource{pull_provenance.py}'
 *     - $flows_root
 *     - $owner
 *   package_shape:
 *     type: process.exec
 *     timeout_ms: 20000
 *     argv:
 *     - python3
 *     - '@resource{_rote_python_c.py}'
 *     - '@resource{package_shape.py}'
 *     - $flows_root
 *     - $owner
 * ---
 */

const presentationSdk = await import("__ROTE_PRESENTATION_SDK__").catch((cause) => {
  throw new Error(
    "This is a rote steps presentation program. Run it with `rote play run <name>`.",
    { cause },
  );
});
const { FlowOutput, loadPresentationContext, stepName } = presentationSdk;

const out = new FlowOutput();
const ctx = await loadPresentationContext();

type Row = {
  ref: string;
  version?: string | null;
  pulled_at?: string | null;
  source?: string | null;
  steps?: number | null;
  bytes?: number | null;
  warnings: string[];
};

// A step that did not complete is reported as a labeled unknown rather than dropped,
// so an absent reading never reads as an empty inventory.
function readStep(
  step: ReturnType<typeof ctx.step>,
  name: string,
): { payload: Record<string, unknown> | null; note: string | null } {
  const status = step.outcome.status;
  if (status !== "completed" && status !== "restored") {
    return { payload: null, note: `${name}: step ${status}` };
  }
  const body = step.outcome.output.body as Record<string, unknown> | undefined;
  const stdout = body?.stdout as { text?: string } | undefined;
  const text = stdout?.text;
  if (typeof text !== "string" || text.trim() === "") {
    return { payload: null, note: `${name}: completed with no output` };
  }
  try {
    return { payload: JSON.parse(text) as Record<string, unknown>, note: null };
  } catch {
    return { payload: null, note: `${name}: output was not the expected JSON` };
  }
}

const provenance = readStep(ctx.step(stepName("pull_provenance")), "pull_provenance");
const shape = readStep(ctx.step(stepName("package_shape")), "package_shape");

const notes: string[] = [];
for (const n of [provenance.note, shape.note]) if (n) notes.push(n);
for (const p of [provenance.payload, shape.payload]) {
  const w = p?.warning;
  if (typeof w === "string") notes.push(w);
}

const rows = new Map<string, Row>();
function ensure(ref: string): Row {
  let r = rows.get(ref);
  if (!r) {
    r = { ref, warnings: [] };
    rows.set(ref, r);
  }
  return r;
}

for (const raw of (provenance.payload?.packages as Array<Record<string, unknown>> | undefined) ?? []) {
  const r = ensure(String(raw.ref));
  r.version = (raw.version as string | null) ?? null;
  r.pulled_at = (raw.pulled_at as string | null) ?? null;
  r.source = (raw.source as string | null) ?? null;
  if (typeof raw.warning === "string") r.warnings.push(raw.warning);
}
for (const raw of (shape.payload?.detail as Array<Record<string, unknown>> | undefined) ?? []) {
  const r = ensure(String(raw.ref));
  r.steps = (raw.steps as number | null) ?? null;
  r.bytes = (raw.bytes as number | null) ?? null;
  if (typeof raw.warning === "string") r.warnings.push(raw.warning);
}

const ordered = [...rows.values()].sort((a, b) => a.ref.localeCompare(b.ref));
const unknown = "unknown";
const kb = (n: number | null | undefined) => (typeof n === "number" ? `${(n / 1024).toFixed(1)} KB` : unknown);
const day = (s: string | null | undefined) => (typeof s === "string" ? s.slice(0, 10) : unknown);

const lines: string[] = [];
if (ordered.length === 0) {
  lines.push("No Play packages are installed here.");
  lines.push("");
  lines.push("Pull one with `rote registry play pull <owner>/<name>` and run this again.");
} else {
  lines.push(`${ordered.length} Play package(s) installed.`);
  lines.push("");
  lines.push("| Play | Version | Pulled | Steps | Size |");
  lines.push("|---|---|---|---:|---:|");
  for (const r of ordered) {
    lines.push(
      `| \`${r.ref}\` | ${r.version ?? unknown} | ${day(r.pulled_at)} | ${r.steps ?? unknown} | ${kb(r.bytes)} |`,
    );
  }
}
for (const r of ordered) {
  for (const w of r.warnings) lines.push(`- ${r.ref}: ${w}`);
}
if (notes.length > 0) {
  lines.push("");
  for (const n of notes) lines.push(`- ${n}`);
}

out.human(lines.join("\n"));
out.summary(
  ordered.length === 0
    ? "No Play packages installed."
    : `${ordered.length} Play package(s) installed.`,
);
out.result({
  run_id: ctx.run.run_id,
  flows_root: (provenance.payload?.flows_root as string | undefined) ??
    (shape.payload?.flows_root as string | undefined) ?? null,
  count: ordered.length,
  packages: ordered,
  notes,
});

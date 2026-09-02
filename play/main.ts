#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: reach-check
 * source: https://play.modiqo.ai/sookra/reach-check
 * description: See what a Play actually reaches on this machine before you publish it or run it. rote play inspect shows what a Play declares and resolves those declarations against your host. This reads the step bodies themselves, follows sh -c, python3 -c and @resource files, and reports the executables, imports, adapters, browser steps, environment variables and writes they really touch, together with which of them are missing here. It never runs, imports or pulls the Play it reads, and it writes nothing. The one subprocess it runs is rote play inspect, to report rote's own verdict beside this one, and offline=true skips it.
 * provenance:
 *   author: sookra <stephensookra@gmail.com>
 *   url: https://play.modiqo.ai/sookra/reach-check
 * metadata:
 *   version: 0.2.3
 *   contract:
 *     atomic: true
 *     input:
 *       type: none
 *     output:
 *       format: json
 *       destination: stdout
 *     composable: true
 *   rote_version: 0.78.0
 *   status: released
 *   kind: atomic
 *   flow_type: parallel
 *   execution_model: steps_with_presentation
 *   requires_sessions: false
 * tags:
 * - domain-rote-plays
 * - job-dependency-audit
 * - audience-play-authors
 * - effect-read-only
 * discoverability:
 *   tags:
 *   - domain-rote-plays
 *   - job-dependency-audit
 *   - audience-play-authors
 *   - effect-read-only
 * parameters:
 * - name: play
 *   type: string
 *   required: false
 *   default: all
 *   description: 'What to read: all, an owner/name reference such as modiqo/hello, or a path to a Play package directory. Use the path form on a Play you are writing, before you publish it.'
 * - name: flows_root
 *   type: string
 *   required: false
 *   default: ~/.rote/flows
 *   description: Directory holding installed packages. Change it to read a rote store other than the default one.
 * - name: strict
 *   type: string
 *   required: false
 *   default: 'false'
 *   description: Set to true to exit non-zero when any analysed Play reaches something that is missing on this machine. Off by default, because a finding is information rather than a failure.
 * - name: offline
 *   type: string
 *   required: false
 *   default: 'false'
 *   description: Set to true to skip the one step that calls rote play inspect, so the run makes no network call at all.
 * steps:
 *   host_facts:
 *     type: process.exec
 *     timeout_ms: 15000
 *     argv:
 *     - python3
 *     - '@resource{_rote_python_c.py}'
 *     - '@resource{host_facts.py}'
 *   reach_analysis:
 *     type: process.exec
 *     timeout_ms: 25000
 *     argv:
 *     - python3
 *     - '@resource{_rote_python_c.py}'
 *     - '@resource{reach_analysis.py}'
 *     - $play
 *     - $flows_root
 *     - $strict
 *   rote_verdict:
 *     type: process.exec
 *     timeout_ms: 25000
 *     argv:
 *     - python3
 *     - '@resource{_rote_python_c.py}'
 *     - '@resource{rote_verdict.py}'
 *     - $play
 *     - $offline
 *     - $flows_root
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

// A step that did not complete becomes a labeled unknown, never a silent empty result.
function readStep(
  step: ReturnType<typeof ctx.step>,
  label: string,
): { payload: Record<string, unknown> | null; note: string | null } {
  const status = step.outcome.status;
  if (status !== "completed" && status !== "restored") {
    return { payload: null, note: `${label}: step ${status}` };
  }
  const body = step.outcome.output.body as Record<string, unknown> | undefined;
  const stdout = body?.stdout as { text?: string } | undefined;
  const text = stdout?.text;
  if (typeof text !== "string" || text.trim() === "") {
    return { payload: null, note: `${label}: completed with no output` };
  }
  try {
    return { payload: JSON.parse(text) as Record<string, unknown>, note: null };
  } catch {
    return { payload: null, note: `${label}: output was not the expected JSON` };
  }
}

const host = readStep(ctx.step(stepName("host_facts")), "host_facts");
const reach = readStep(ctx.step(stepName("reach_analysis")), "reach_analysis");
const verdict = readStep(ctx.step(stepName("rote_verdict")), "rote_verdict");

const notes: string[] = [];
for (const n of [host.note, reach.note, verdict.note]) if (n) notes.push(n);
for (const p of [reach.payload, verdict.payload]) {
  const w = p?.warning;
  if (typeof w === "string") notes.push(w);
}

type Pkg = Record<string, unknown>;
const packages = (reach.payload?.packages as Pkg[] | undefined) ?? [];
const verdicts = (verdict.payload?.verdicts as Record<string, Record<string, unknown>> | undefined) ?? {};

const list = (v: unknown): string[] => Array.isArray(v) ? v.map(String) : [];
const join = (v: unknown): string => {
  const a = list(v);
  return a.length === 0 ? "none" : a.join(", ");
};

const lines: string[] = [];
// count is the number READ; `packages` may be trimmed for output size, so never use its length.
const count = Number(reach.payload?.count ?? packages.length);
const detailed = Number(reach.payload?.detailed ?? packages.length);
const trimmed = Number(reach.payload?.trimmed ?? 0);
const undeclaredTotal = Number(reach.payload?.undeclared_total ?? 0);
const missingTotal = Number(reach.payload?.missing_total ?? 0);

if (count === 0) {
  lines.push("No Play package matched.");
  lines.push("");
  lines.push("Point this at a package you are writing with `play=/path/to/your-play`,");
  lines.push("or pull one with `rote registry play pull <owner>/<name>` and run it again.");
} else {
  lines.push(
    `${count} package(s) read. ${undeclaredTotal} reach(es) that no manifest declares. ${missingTotal} of those are missing on this machine.`,
  );
  if (trimmed > 0) {
    lines.push("");
    lines.push(
      `The counts above cover all ${count}. The table below shows ${detailed} of them, the ones with a finding first; ${trimmed} clean row(s) are not listed.`,
    );
  }
  lines.push("");
  lines.push("| Play | Declared | Reached | Undeclared but reached | Missing here | rote says |");
  lines.push("|---|---:|---:|---|---|---|");
  for (const p of packages) {
    const ref = String(p.ref ?? "unknown");
    if (p.unreadable) {
      lines.push(`| \`${ref}\` | ? | ? | unreadable | unreadable | |`);
      continue;
    }
    const v = verdicts[ref];
    let says = "not checked";
    if (v && v.available === true) {
      says = v.play_run_eligible === true ? "eligible" : "not eligible";
      const b = list(v.blockers);
      if (b.length > 0) says += `: ${b.join("; ")}`;
    } else if (v && typeof v.warning === "string") {
      says = "unknown";
    }
    lines.push(
      `| \`${ref}\` | ${list(p.declared).length} | ${list(p.reached).length} | ${join(p.undeclared_but_reached)} | ${join(p.missing_here)} | ${says} |`,
    );
  }

  const detail: string[] = [];
  for (const p of packages) {
    const ref = String(p.ref ?? "unknown");
    const und = list(p.undeclared_but_reached);
    const miss = list(p.missing_here);
    if (und.length > 0) {
      detail.push(
        `- \`${ref}\` reaches ${und.length} executable(s) its manifest does not declare${miss.length > 0 ? `, and ${miss.length} of those ${miss.length === 1 ? "is" : "are"} not on this machine` : ""}.`,
      );
    }
    const wc = list(p.writes_cwd).length;
    const wo = list(p.writes_outside_cwd).length;
    if (wc > 0 || wo > 0) {
      detail.push(`- \`${ref}\` writes: ${wc} inside the working directory, ${wo} outside it.`);
    }
    const ad = list(p.adapters_reached);
    if (ad.length > 0) detail.push(`- \`${ref}\` calls adapter(s): ${ad.join(", ")}.`);
    if (p.needs_browser === true) detail.push(`- \`${ref}\` has browser steps, which need the full install.`);
    const env = list(p.env_vars_read);
    if (env.length > 0) detail.push(`- \`${ref}\` reads environment variable(s): ${env.join(", ")}.`);
    const dyn = list(p.dynamic);
    if (dyn.length > 0) detail.push(`- \`${ref}\` builds ${dyn.length} call target(s) at run time, which cannot be resolved by reading: ${dyn.join(", ")}.`);
    const mm = list(p.missing_modules);
    if (mm.length > 0) detail.push(`- \`${ref}\` imports module(s) not installed here: ${mm.join(", ")}.`);
    // A body this tool could not read must be named. Reporting nothing for it would let a
    // package look clean when it was simply never analysed.
    const un = Array.isArray(p.unanalysed_bodies)
      ? (p.unanalysed_bodies as Array<Record<string, unknown>>)
      : [];
    for (const u of un) {
      detail.push(
        `- \`${ref}\` step \`${String(u.step ?? "?")}\`: ${String(u.resource ?? "body")} was not read, ${String(u.reason ?? "reason unknown")}.`,
      );
    }
    for (const n of list(p.notes)) detail.push(`- \`${ref}\`: ${n}`);
  }
  if (detail.length > 0) {
    lines.push("");
    lines.push("What that means");
    lines.push(...detail);
  }
}

const hp = host.payload;
if (hp) {
  lines.push("");
  lines.push(
    `Read with python ${String(hp.python ?? "?")} on ${String(hp.platform ?? "?")} ${String(hp.machine ?? "")}, standard library resolved via ${String(hp.stdlib_source ?? "?")}, across ${String(hp.path_entries ?? "?")} PATH entries.`,
  );
}
lines.push(
  "`rote play inspect` reports what a Play declares and resolves that against this host. This reports what its steps reach. Reachability is evidence, not a guarantee of safety. This never runs, imports or pulls the Play it reads and writes nothing; the only subprocess it runs is `rote play inspect` itself, which `offline=true` skips.",
);
if (notes.length > 0) {
  lines.push("");
  for (const n of notes) lines.push(`- ${n}`);
}

out.human(lines.join("\n"));
out.summary(
  count === 0
    ? "No Play package matched."
    : `${count} package(s): ${undeclaredTotal} undeclared reach(es), ${missingTotal} missing here.`,
);
out.result({
  run_id: ctx.run.run_id,
  target: reach.payload?.target ?? null,
  flows_root: reach.payload?.flows_root ?? null,
  count,
  undeclared_total: undeclaredTotal,
  missing_total: missingTotal,
  host: hp ?? null,
  packages,
  rote_verdicts: verdicts,
  notes,
});

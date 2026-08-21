// Verifies scripts/60-greenboost-fan.rules against the privilege-escalation
// case it exists to prevent.  Loads the REAL rule file and evaluates it
// against a minimal polkit stub, so the test cannot drift from the shipped
// rule the way a re-implementation of its logic would.
//
// Run: node tests/polkit_rule_test.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const RULE = path.join(__dirname, "..", "scripts", "60-greenboost-fan.rules");

let registered = null;
const polkit = {
  Result: { YES: "YES", NO: "NO", AUTH_SELF: "AUTH_SELF",
            AUTH_ADMIN: "AUTH_ADMIN", NOT_HANDLED: "NOT_HANDLED" },
  addRule: (fn) => { registered = fn; },
  log: () => {},
};
vm.runInNewContext(fs.readFileSync(RULE, "utf8"), { polkit }, { filename: RULE });
if (typeof registered !== "function") {
  console.error("FAIL: the rule file registered no polkit.addRule callback");
  process.exit(1);
}

const decide = (program, command_line, opts = {}) => {
  const lookup = { program, command_line };
  const action = { id: opts.id || "org.freedesktop.policykit.exec",
                   lookup: (k) => lookup[k] };
  const subject = { local: opts.local !== false, active: opts.active !== false };
  return registered(action, subject) === polkit.Result.YES;
};

const P  = "/usr/bin/python3";
const G  = "/usr/local/lib/greenboost-gaming/gb_gaming/nvml_fan.py";
const G2 = "/usr/lib/greenboost-gaming/gb_gaming/nvml_fan.py";

const cases = [
  // --- the helper's real invocations, as fan_daemon.py issues them ---
  [true,  P, `${P} ${G} auto`,     {}, "auto"],
  [true,  P, `${P} ${G} set 0`,    {}, "set floor"],
  [true,  P, `${P} ${G} set 60`,   {}, "set mid"],
  [true,  P, `${P} ${G} set 100`,  {}, "set ceiling"],
  [true,  P, `${P} ${G2} set 45`,  {}, "packaged /usr/lib path"],
  [true,  P, `python3 ${G} auto`,  {}, "unresolved argv[0]"],

  // --- the vulnerability this rule was rewritten to close ---
  [false, P, `${P} /tmp/evil.py nvml_fan.py`,          {}, "EXPLOIT: substring in an argument"],
  [false, P, `${P} /tmp/evil.py --flag=nvml_fan.py`,   {}, "EXPLOIT: substring in a flag"],
  [false, P, `${P} /home/x/nvml_fan.py auto`,          {}, "EXPLOIT: same basename, attacker path"],
  [false, P, `${P} -c import os;os.system('sh') ${G} auto`, {}, "EXPLOIT: -c before the script"],
  [false, P, `${P} ${G} auto ; /tmp/evil.sh`,          {}, "EXPLOIT: trailing command"],

  // --- outside the helper's own argument grammar ---
  [false, P, `${P} ${G} set 101`,   {}, "speed above 100"],
  [false, P, `${P} ${G} set 9999`,  {}, "speed far out of range"],
  [false, P, `${P} ${G} query`,     {}, "unknown action"],
  [false, P, `${P} ${G}`,           {}, "no action"],
  [false, P, `${P} /usr/local/lib/greenboost-gaming/gb_gaming/nvml_fanXpy auto`, {}, "dot is not a wildcard"],

  // --- adjacent helpers and interpreters are not granted here ---
  [false, P, `${P} /usr/local/lib/greenboost-gaming/gb_gaming/nvml_control.py query`, {}, "nvml_control.py (sudoers only)"],
  [false, "/usr/bin/perl", `/usr/bin/perl ${G} auto`, {}, "non-python interpreter"],
  [false, P, `prefix${P} ${G} auto`, {}, "unanchored prefix"],

  // --- subject / action gating ---
  [false, P, `${P} ${G} auto`, { local: false },  "remote subject"],
  [false, P, `${P} ${G} auto`, { active: false }, "inactive session"],
  [false, P, `${P} ${G} auto`, { id: "org.freedesktop.systemd1.manage-units" }, "unrelated action id"],
];

let failed = 0;
for (const [want, prog, cmdline, opts, label] of cases) {
  const got = decide(prog, cmdline, opts);
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? "  ok  " : "FAIL  "}${want ? "ALLOW" : "DENY "}  ${label}`);
}
console.log(`\n${cases.length - failed}/${cases.length} passed`);
process.exit(failed ? 1 : 0);

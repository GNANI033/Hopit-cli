"""
hopit/kubernetes.py
────────────────────────────────────────────────────────────────────────────
Kubernetes support for hopit-cli.

Provides TWO layers:
  1. Simple-English verbs (e.g. "k8s pods", "k8s deploy myapp", "k8s logs mypod")
     that map to the right kubectl invocations automatically.
  2. Raw kubectl pass-through ("kubectl get pods -n kube-system") with proper
     terminal output piped through hopit's renderer.

Everything in this module is importable so that commands.py, ui.py, and main.py
can call the helpers they need.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Helper: is kubectl available?
# ─────────────────────────────────────────────────────────────────────────────

def kubectl_available() -> bool:
    return shutil.which("kubectl") is not None


# ─────────────────────────────────────────────────────────────────────────────
# Live resource loaders (used by the autocomplete engine)
# ─────────────────────────────────────────────────────────────────────────────

def _run_kubectl_silent(*args: str) -> list[str]:
    """Run kubectl and return stdout lines; return [] on any error."""
    if not kubectl_available():
        return []
    try:
        r = subprocess.run(
            ["kubectl"] + list(args),
            capture_output=True, text=True, errors="ignore", timeout=4
        )
        if r.returncode != 0:
            return []
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def load_namespaces() -> list[str]:
    lines = _run_kubectl_silent("get", "namespaces", "--no-headers", "-o",
                                "custom-columns=NAME:.metadata.name")
    return lines or ["default", "kube-system", "kube-public"]


def load_pods(namespace: str = "") -> list[str]:
    args = ["get", "pods", "--no-headers", "-o",
            "custom-columns=NAME:.metadata.name"]
    if namespace and namespace != "all":
        args += ["-n", namespace]
    else:
        args.append("--all-namespaces")
    lines = _run_kubectl_silent(*args)
    # When --all-namespaces is used, first column is namespace
    if not namespace or namespace == "all":
        pods = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                pods.append(parts[1])
        return pods
    return lines


def load_deployments(namespace: str = "default") -> list[str]:
    return _run_kubectl_silent(
        "get", "deployments", "-n", namespace, "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name"
    )


def load_services(namespace: str = "default") -> list[str]:
    return _run_kubectl_silent(
        "get", "services", "-n", namespace, "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name"
    )


def load_nodes() -> list[str]:
    return _run_kubectl_silent(
        "get", "nodes", "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name"
    )


def load_configmaps(namespace: str = "default") -> list[str]:
    return _run_kubectl_silent(
        "get", "configmaps", "-n", namespace, "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name"
    )


def load_secrets(namespace: str = "default") -> list[str]:
    return _run_kubectl_silent(
        "get", "secrets", "-n", namespace, "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name"
    )


def load_contexts() -> list[str]:
    lines = _run_kubectl_silent("config", "get-contexts", "--no-headers",
                                "-o", "name")
    return lines


def load_containers_in_pod(pod: str, namespace: str = "default") -> list[str]:
    """Return container names inside a specific pod."""
    lines = _run_kubectl_silent(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.spec.containers[*].name}"
    )
    if lines:
        return lines[0].split()
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Simple-English command builder
# Converts user's natural language into a kubectl argv list.
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (verb_aliases, kubectl_args_template, description)
SIMPLE_COMMANDS: list[tuple[tuple[str, ...], str, str]] = [
    # ── Pod inspection ───────────────────────────────────────────────────────
    (("pods", "pod", "list pods", "get pods"),
     "get pods -o wide",
     "List all pods in the current namespace"),

    (("pods all", "all pods"),
     "get pods --all-namespaces -o wide",
     "List pods across ALL namespaces"),

    (("pod info", "describe pod"),
     "describe pod {name}",
     "Show detailed info about a pod"),

    (("logs", "pod logs"),
     "logs {name}",
     "Show logs from a pod (last 100 lines)"),

    (("follow", "tail", "live logs"),
     "logs -f {name}",
     "Follow (tail -f) logs from a pod live"),

    (("exec", "shell", "bash into"),
     "exec -it {name} -- /bin/bash",
     "Open an interactive shell inside a pod"),

    (("sh", "sh into"),
     "exec -it {name} -- /bin/sh",
     "Open /bin/sh shell inside a pod"),

    # ── Deployments ──────────────────────────────────────────────────────────
    (("deployments", "deploy", "deploys", "list deploy"),
     "get deployments -o wide",
     "List all deployments"),

    (("deployment info", "describe deploy"),
     "describe deployment {name}",
     "Show detailed info about a deployment"),

    (("scale",),
     "scale deployment {name} --replicas={count}",
     "Scale a deployment to N replicas"),

    (("rollout status",),
     "rollout status deployment/{name}",
     "Check rollout status of a deployment"),

    (("rollout history",),
     "rollout history deployment/{name}",
     "View rollout history of a deployment"),

    (("rollout undo",),
     "rollout undo deployment/{name}",
     "Roll back a deployment to the previous version"),

    (("restart",),
     "rollout restart deployment/{name}",
     "Restart all pods in a deployment"),

    # ── Services ─────────────────────────────────────────────────────────────
    (("services", "svc", "list services"),
     "get services -o wide",
     "List all services"),

    (("service info", "describe service"),
     "describe service {name}",
     "Show detailed info about a service"),

    # ── Nodes ────────────────────────────────────────────────────────────────
    (("nodes", "node", "list nodes"),
     "get nodes -o wide",
     "List all cluster nodes"),

    (("node info", "describe node"),
     "describe node {name}",
     "Show detailed info about a node"),

    (("drain",),
     "drain {name} --ignore-daemonsets --delete-emptydir-data",
     "Safely drain a node for maintenance"),

    (("cordon",),
     "cordon {name}",
     "Mark a node as unschedulable"),

    (("uncordon",),
     "uncordon {name}",
     "Mark a node as schedulable again"),

    # ── Namespaces ───────────────────────────────────────────────────────────
    (("namespaces", "ns", "list ns"),
     "get namespaces",
     "List all namespaces"),

    (("create namespace", "new namespace", "new ns"),
     "create namespace {name}",
     "Create a new namespace"),

    (("delete namespace", "del namespace", "remove namespace"),
     "delete namespace {name}",
     "Delete a namespace (and all its resources)"),

    # ── Config Maps & Secrets ────────────────────────────────────────────────
    (("configmaps", "cm", "configs"),
     "get configmaps",
     "List all ConfigMaps"),

    (("secrets",),
     "get secrets",
     "List all Secrets"),

    # ── Apply / Delete resources ─────────────────────────────────────────────
    (("apply",),
     "apply -f {file}",
     "Apply (create/update) Kubernetes manifests from a file or URL"),

    (("delete",),
     "delete -f {file}",
     "Delete Kubernetes resources defined in a file"),

    (("delete pod",),
     "delete pod {name}",
     "Force-delete a specific pod"),

    # ── Cluster info ─────────────────────────────────────────────────────────
    (("cluster info", "cluster"),
     "cluster-info",
     "Display Kubernetes cluster info and API endpoint"),

    (("events",),
     "get events --sort-by=.lastTimestamp",
     "Show recent cluster events sorted by time"),

    (("top pods",),
     "top pods",
     "Show CPU/Memory usage of pods (requires metrics-server)"),

    (("top nodes",),
     "top nodes",
     "Show CPU/Memory usage of nodes (requires metrics-server)"),

    # ── Context / Config ─────────────────────────────────────────────────────
    (("contexts", "context list"),
     "config get-contexts",
     "List all available kubectl contexts"),

    (("use context", "switch context", "use cluster"),
     "config use-context {name}",
     "Switch active kubectl context (cluster)"),

    (("current context", "who am i"),
     "config current-context",
     "Show the currently active kubectl context"),

    # ── Port forwarding ──────────────────────────────────────────────────────
    (("forward", "port-forward", "portforward"),
     "port-forward pod/{name} {local}:{remote}",
     "Forward a local port to a port on a pod"),

    # ── Generic get ─────────────────────────────────────────────────────────
    (("get",),
     "get {resource} -o wide",
     "Generic: get any Kubernetes resource type"),
]


def build_simple_verb_map() -> dict[str, tuple[str, str]]:
    """Returns {verb: (kubectl_template, description)} for all simple commands."""
    verb_map: dict[str, tuple[str, str]] = {}
    for aliases, template, desc in SIMPLE_COMMANDS:
        for alias in aliases:
            verb_map[alias.lower()] = (template, desc)
    return verb_map

VERB_MAP = build_simple_verb_map()


# ─────────────────────────────────────────────────────────────────────────────
# k8s command builder — called by commands.py
# ─────────────────────────────────────────────────────────────────────────────

def k8s_cmd(arg: str) -> list[str]:
    """
    Build the argv for a 'k8s <arg>' invocation.
    Returns a list suitable for subprocess.run().
    Handles:
      - Simple-English verbs (e.g. "pods", "logs mypod", "restart myapp")
      - Raw kubectl pass-through (any unrecognized input starting with a
        kubectl subcommand like get/apply/delete/describe etc.)
    """
    if not arg:
        # With no arg, show pods (most common "what's running?" check)
        return ["kubectl", "get", "pods", "-o", "wide"]

    tokens = shlex.split(arg)
    if not tokens:
        return ["kubectl", "get", "pods", "-o", "wide"]

    # Check simple-english verb (1 or 2 token match)
    verb1 = tokens[0].lower()
    verb2 = (tokens[0] + " " + tokens[1]).lower() if len(tokens) >= 2 else ""

    # Try 2-word match first
    if verb2 and verb2 in VERB_MAP:
        template, _ = VERB_MAP[verb2]
        rest = tokens[2:]
        return _fill_template(template, rest)

    # Try 1-word match
    if verb1 in VERB_MAP:
        template, _ = VERB_MAP[verb1]
        rest = tokens[1:]
        return _fill_template(template, rest)

    # Raw kubectl pass-through — just prepend kubectl
    return ["kubectl"] + tokens


def _fill_template(template: str, rest: list[str]) -> list[str]:
    """
    Fills {name}, {file}, {count}, {local}, {remote} placeholders in
    a kubectl template string, then splits into an argv list.
    """
    filled = template
    positional = list(rest)

    def take() -> str:
        return positional.pop(0) if positional else ""

    # Replace known placeholders in order
    if "{name}" in filled:
        filled = filled.replace("{name}", take(), 1)
    if "{file}" in filled:
        filled = filled.replace("{file}", take(), 1)
    if "{count}" in filled:
        filled = filled.replace("{count}", take() or "1", 1)
    if "{resource}" in filled:
        filled = filled.replace("{resource}", take() or "pods", 1)
    if "{local}" in filled:
        val = take() or "8080"
        filled = filled.replace("{local}", val, 1)
    if "{remote}" in filled:
        val = take() or "8080"
        filled = filled.replace("{remote}", val, 1)

    # Append any leftover tokens
    base = shlex.split(filled)
    return ["kubectl"] + base + positional


# ─────────────────────────────────────────────────────────────────────────────
# Completion data (structured subcommand map for the UI layer)
# ─────────────────────────────────────────────────────────────────────────────

# Top-level subcommand completions for "k8s <TAB>"
K8S_TOP_COMPLETIONS: list[tuple[str, str]] = [
    # ── Pods ──
    ("pods",           "📦 List pods in the current namespace"),
    ("pods all",       "📦 List pods across all namespaces"),
    ("pod info",       "🔍 Describe a specific pod"),
    ("logs",           "📋 Show logs from a pod"),
    ("follow",         "🔴 Follow (tail) live logs from a pod"),
    ("exec",           "💻 Open bash shell inside a pod"),
    ("sh",             "💻 Open /bin/sh shell inside a pod"),
    # ── Deployments ──
    ("deployments",    "🚀 List all deployments"),
    ("deployment info","🔍 Describe a specific deployment"),
    ("scale",          "⚖️  Scale a deployment to N replicas"),
    ("restart",        "♻️  Restart all pods in a deployment"),
    ("rollout status", "📊 Check deployment rollout status"),
    ("rollout history","📜 View deployment rollout history"),
    ("rollout undo",   "⏪ Roll back a deployment"),
    # ── Services ──
    ("services",       "🌐 List all services"),
    ("service info",   "🔍 Describe a specific service"),
    # ── Nodes ──
    ("nodes",          "🖥️  List all cluster nodes"),
    ("node info",      "🔍 Describe a specific node"),
    ("drain",          "🔧 Drain a node for maintenance"),
    ("cordon",         "🔒 Mark a node as unschedulable"),
    ("uncordon",       "🔓 Mark a node as schedulable"),
    # ── Namespaces ──
    ("namespaces",     "📁 List all namespaces"),
    ("create namespace","➕ Create a new namespace"),
    ("delete namespace","❌ Delete a namespace"),
    # ── Config ──
    ("configmaps",     "⚙️  List all ConfigMaps"),
    ("secrets",        "🔑 List all Secrets"),
    ("events",         "🗓️  Show recent cluster events"),
    ("top pods",       "📈 Show pod CPU/Memory usage"),
    ("top nodes",      "📈 Show node CPU/Memory usage"),
    ("cluster info",   "ℹ️  Display cluster API endpoint info"),
    # ── Context ──
    ("contexts",       "🌍 List all kubectl contexts"),
    ("use context",    "🔀 Switch active kubectl context"),
    ("current context","👤 Show current kubectl context"),
    # ── Apply/Delete ──
    ("apply",          "✅ Apply a Kubernetes manifest file"),
    ("delete",         "🗑️  Delete resources from a manifest file"),
    ("delete pod",     "🗑️  Force-delete a specific pod"),
    # ── Port forward ──
    ("forward",        "🔗 Forward a local port to a pod port"),
    # ── Generic ──
    ("get",            "🔎 Get any Kubernetes resource type"),
]

# Full list of raw kubectl subcommands for pass-through completions
KUBECTL_SUBCOMMANDS: list[tuple[str, str]] = [
    ("get",          "Display one or many resources"),
    ("describe",     "Show details of a specific resource"),
    ("create",       "Create a resource from a file or stdin"),
    ("apply",        "Apply a configuration to a resource"),
    ("delete",       "Delete resources by filenames, stdin, or names"),
    ("edit",         "Edit a resource in the default editor"),
    ("exec",         "Execute a command in a container"),
    ("logs",         "Print the logs for a container in a pod"),
    ("port-forward", "Forward one or more local ports to a pod"),
    ("scale",        "Set a new size for a deployment or replica set"),
    ("rollout",      "Manage the rollout of a deployment"),
    ("set",          "Set specific features on objects"),
    ("label",        "Update the labels on a resource"),
    ("annotate",     "Update the annotations on a resource"),
    ("patch",        "Update fields of a resource"),
    ("replace",      "Replace a resource by filename or stdin"),
    ("expose",       "Expose a resource as a new Kubernetes Service"),
    ("run",          "Run a particular image in the cluster"),
    ("attach",       "Attach to a running container"),
    ("cp",           "Copy files/dirs to and from containers"),
    ("auth",         "Inspect authorization"),
    ("top",          "Display resource (CPU/memory) usage"),
    ("cluster-info", "Display cluster info and API endpoint"),
    ("config",       "Modify kubeconfig files"),
    ("version",      "Print the client and server version information"),
    ("api-resources","Print the supported API resources on the server"),
    ("api-versions", "Print the supported API versions on the server"),
    ("explain",      "Get documentation for a resource"),
    ("cordon",       "Mark node as unschedulable"),
    ("uncordon",     "Mark node as schedulable"),
    ("drain",        "Drain node in preparation for maintenance"),
    ("taint",        "Update the taints on one or more nodes"),
    ("events",       "List events"),
    ("wait",         "Experimental: Wait for a specific condition on resources"),
    ("diff",         "Diff live version against would-be applied version"),
    ("kustomize",    "Print a set of API resources generated from a kustomization"),
]

KUBECTL_RESOURCE_TYPES: list[tuple[str, str]] = [
    ("pods",                 "Running workloads (pod)"),
    ("deployments",          "Manages pod replicas (deploy)"),
    ("services",             "Network endpoints (svc)"),
    ("namespaces",           "Virtual clusters (ns)"),
    ("nodes",                "Cluster machines (node)"),
    ("configmaps",           "Non-secret config data (cm)"),
    ("secrets",              "Sensitive config data"),
    ("replicasets",          "Ensures N replicas of pods (rs)"),
    ("statefulsets",         "Ordered, persistent pod sets (sts)"),
    ("daemonsets",           "Ensures pod on every node (ds)"),
    ("jobs",                 "Run-to-completion workloads"),
    ("cronjobs",             "Scheduled periodic jobs (cj)"),
    ("ingresses",            "Manage external HTTP(S) access (ing)"),
    ("persistentvolumes",    "Cluster-level storage (pv)"),
    ("persistentvolumeclaims","Namespaced storage requests (pvc)"),
    ("serviceaccounts",      "Pod identity accounts (sa)"),
    ("clusterroles",         "Cluster-wide RBAC roles"),
    ("rolebindings",         "Namespace-scoped RBAC bindings"),
    ("endpoints",            "IP endpoints for a service (ep)"),
    ("events",               "Cluster event records (ev)"),
    ("horizontalpodautoscalers","Auto-scale based on metrics (hpa)"),
    ("networkpolicies",      "Pod network traffic rules (netpol)"),
    ("resourcequotas",       "Resource usage limits per ns (quota)"),
    ("limitranges",          "Enforce min/max resource limits (limits)"),
]


# ─────────────────────────────────────────────────────────────────────────────
# __main__ entry-point (for subprocess invocation)
# ─────────────────────────────────────────────────────────────────────────────

from hopit.config import safe_entrypoint

@safe_entrypoint
def main():
    from hopit.config import console

    if not kubectl_available():
        console.print("[bold red]kubectl not found.[/bold red] "
                      "Install it: https://kubernetes.io/docs/tasks/tools/")
        sys.exit(1)

    arg = " ".join(sys.argv[1:])
    cmd = k8s_cmd(arg)
    # Just exec so we get live terminal output (colours, etc.)
    try:
        result = subprocess.run(cmd)
        sys.exit(result.returncode)
    except FileNotFoundError:
        console.print(f"[red]Command not found: {cmd[0]}[/red]")
        sys.exit(1)

if __name__ == "__main__":
    main()

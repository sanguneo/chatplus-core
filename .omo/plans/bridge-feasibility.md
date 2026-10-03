# ChattingPlus send bridge: feasibility

Date: 2026-10-03. Architecture assessment only; no client launch, process
inspection/attachment, injection, binary-analysis run, or test send was performed.

## Verdict

**NO-GO for a maintainable unattended saya send bridge within an hours-sized
budget, on the evidence available.** This is a budget verdict, not a claim that
the bridge is technically impossible. An existing callable automation endpoint
could change it. Otherwise, prefer a JVM agent over Dart-AOT/native patching, but
treat that as a separate discovery and compatibility project.

The hard part is not merely loading code. It is identifying the actual send
entry point, obtaining the *existing authenticated instance*, dispatching on its
required executor, and preserving the client's callbacks and local persistence.
Loading a JAR or finding a method named `send` proves none of these.

Keep `messages.send` absent until that path works end to end. The existing
`Sender` seam, serialized write line, and local read-back can be reused; they
do not supply the missing client bridge. A local stored message is not proof
of network delivery, and an uncertain send must not be retried automatically.

## What is known and what remains an inference

The supplied Flutter/Dart-AOT, in-process JVM, JNI exports, MSIX installation,
and failed background-input observations are accepted as given. JNI exports
establish a Java boundary, **not that Java owns RCS sending**. Java may implement
only another subsystem; that ownership remains unverified.

Repository evidence: `README.md`, `chatplus_core/api.py` (capability declaration),
`chatplus_core/sender.py`, and `chatplus_core/send.py`. PirateTalk's root README
and current `AGENTS.md` establish the in-process bridge precedent and its
captured live context/read-back requirements. Its older
`docs/rust-inprocess-bridge.md` still describes GUI bootstrap paths that the
current rules say were removed; those are not a design to copy.

## 1. Can an external caller avoid loading code?

| Candidate | Assessment |
| --- | --- |
| Existing loopback HTTP/RPC, named pipe, COM automation, app service, or registered URI/activation handler | Best possibility. It must already expose a send operation or route to one in the running authenticated client. Package identity and Flutter do not supply such an API automatically. A single-instance activation pipe or compose URI may only open a window, not submit. No such interface is established here. |
| `package:jni`, `GetJavaVM`, `GetJniEnv`, `JNI_GetCreatedJavaVMs` | Not cross-process APIs. Exported functions and JVM/object pointers belong to the process containing them. Loading the DLL in the adapter cannot retrieve the client's heap or session. `AttachCurrentThread` attaches a native thread already in that process; it does not attach an outside process. `JNIEnv` is thread-local. |
| JVM Attach API | Potentially available even in an embedded JVM hosted by a non-`java.exe` process, depending on VM implementation, build, policy, and access rights. Attach can expose diagnostics and load agents; it is not an arbitrary application-method RPC API. Loading a Java/JVMTI agent is in-process code loading, even though the JVM provides the mechanism. Neither successful attachment nor failure to list a VM alone settles send feasibility. |
| Already enabled JMX/JDWP or Flutter debug service | Existing JMX can invoke a send only if an application MBean exposes it; stock JVM management beans do not. An already enabled Java debugger can technically invoke methods with the right object/thread context, but suspension, callbacks, and private implementation details make it a poor production bridge. Starting an agent is not a non-loading workaround. Release Dart-AOT normally lacks a usable Dart VM service; Flutter driver/test extensions require application support. None is known to be enabled. |
| Separate JVM with the same Java library | Same code, different heap, singleton instances, native handles, connections, and lifecycle. RCS desktop integration may use carrier registration or a paired-device/cloud session; the exact model here is unknown. Cache decryption keys are not evidence of reusable send credentials. Copying tokens does not transfer a live registration/connection and may introduce a competing session or refresh state. This only works cleanly if the library is already a client of an independently running shared broker with a supported interface; then use that interface. Otherwise it is a second client/session project, not reuse of the official client's live path. |

There are still *conditional* Windows UI avenues: other implemented UI Automation
patterns such as `Invoke`/selection, legacy accessibility actions, a registered
share/compose handler, or a user-launched interactive helper receiving legitimate
foreground activation. WinAppDriver/Power Automate cannot manufacture missing
Flutter semantics. `TextPattern` is not a generic text setter. An interactive
helper may support assisted foreground sending, but not reliable background
operation while the desktop is locked or the user is working elsewhere.

The README's suggestion that a signed `uiAccess` helper is **required** is too
strong. Foreground arbitration, UIPI/integrity restrictions, and missing provider
behavior are different problems. `uiAccess` can relax certain UI restrictions,
with signing/install requirements, but does not implement missing actions,
guarantee recipient verification, or prove that `SetValue` will work. Do not
spend the budget on signing before identifying the actual blocker.

## 2. Package-preserving in-process options

**MSIX protects package files; it does not automatically make the process a
protected process.** A packaged full-trust desktop application can run at normal
user integrity. AppContainer status, process access controls, architecture, and
code-loading mitigations are separate facts. Same user/integrity makes some
approaches plausible, not guaranteed. None below requires changing WindowsApps.

| Option, in preference order | Preconditions and update cost |
| --- | --- |
| JVM-supported dynamic Java agent; native JVMTI agent only if needed | Best conditional fit if Java owns sending. Requires supported/enabled attach and agent loading, target-readable agent files, suitable runtime modules, and access to the live objects/class loader. Uses runtime APIs rather than native instruction offsets. Java class/method changes, obfuscation, module access, and runtime policy updates still break the application binding. JEP 451 warns about dynamic agents in JDK 21 and prepares for stricter defaults; inspect the actual bundled version/settings rather than assuming current system Java behavior. |
| Startup Java/JVMTI agent through an existing configurable JVM launch path | More predictable initialization and less dependence on dynamic-agent policy. Only realistic if the app actually accepts external JVM options and the chosen package activation path preserves them. `JAVA_TOOL_OPTIONS` may reach a JNI-created JVM, but is not a guaranteed configuration seam for this app. Requires a client restart and proper package identity; no established seam means no hours-budget shortcut. |
| Explicit same-user native bridge DLL loading into the existing process | Technically plausible for an ordinary full-trust process with the necessary access and no blocking mitigation. The DLL can live outside the package and use JNI from inside rather than alter Dart-AOT code. Loader compatibility is only half the work: class-loader discovery, existing service objects, references, thread attachment, executor dispatch, and callbacks remain. More OS/runtime failure modes than a Java agent; not a maintained bridge merely because one load succeeds. |
| Targeted Windows hook DLL or debugger/instrumentation framework | Can provide an in-process foothold in compatible processes without package writes. Hooks depend on applicable target threads/message processing; Flutter has no generic send hook. Instrumentation frameworks are useful research scaffolding, not automatically compatible with every embedded JVM. Extra lifetime, scheduling, and tool-version dependencies make these worse production defaults. |
| Direct Dart-AOT/native address hooks | Last resort, highest maintenance cost: compiler changes, relocation, ABI/object layouts, thread/isolate rules, and app updates. A JNI export is preferable to an offset, but still not a stable application send API. Reject for this budget. |

Copying/repacking the app or adding a proxy DLL to a different directory is not
equivalent to PirateTalk's beside-the-executable deployment: identity, signing,
package dependencies, data access, and updates can change. PSF/modification
packages are packaging/activation mechanisms, not a universal way to retrofit a
loader into an already running third-party package. Reject these as shortcuts.

If a bridge is later justified, use:
`chatplus-core Sender -> authenticated local IPC -> in-client bridge ->
existing send service/executor`. Preserve package activation and normal login.
Bind requests to the current account and exact conversation, invalidate captured
objects across logout/restart, serialize writes through confirmation, and refuse
unknown app versions until checked. Prefer a returned client message identifier
for read-back correlation; same-text matching alone can confuse a concurrent
manual send with the bridge's attempt. An IPC timeout after dispatch is unknown,
not evidence that nothing was sent.

## 3. Single highest-value next investigation

**Timebox one offline, read-only inventory of the package's integration boundary
to 1-2 hours.** Review the manifest, bundled runtime release/configuration,
existing bootstrap metadata, and JAR/class metadata without executing packaged
code or attaching to the client. Produce one small boundary map identifying:

- Any declared external activation/automation service and whether it submits
  versus merely composes.
- The bundled JVM implementation/version and configured agent/debug facilities.
- Whether a Java class actually owns the send operation, and whether the
  authenticated instance and dispatch executor have identifiable accessors.

This tests the central unproven assumption before investing in a loader.
Static evidence cannot prove a live endpoint or agent works; mark that explicitly.
If a supported external send interface emerges, reopen a narrowly scoped
integration. If only a clear Java service emerges, price a separate agent
feasibility spike. If neither emerges within the timebox, retain the NO-GO and
the read-only adapter; do not expand into weeks of Dart-AOT exploration.

## Platform references

- [JNI Invocation API: VM and thread attachment](https://docs.oracle.com/en/java/javase/21/docs/specs/jni/invocation.html).
- [Attach API: VirtualMachine and agent loading](https://docs.oracle.com/en/java/javase/21/docs/api/jdk.attach/com/sun/tools/attach/VirtualMachine.html).
- [JEP 451: dynamic-agent policy and its distinction from diagnostics](https://openjdk.org/jeps/451).
- [Microsoft: package identity versus full trust/AppContainer](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes).
- [Microsoft: foreground activation restrictions](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow).
- [Microsoft: UI Automation security and UIAccess](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-security-overview).

Validation: repository source/document review and platform documentation only.
No runtime feasibility, successful send, or cross-version compatibility is
claimed. Only this recommendation file was created.

# Agent working instructions

Read [context.md](context.md) and its linked canonical HUTCH Resolve context, Harry plan and contracts before work. If a sibling Resolve checkout exists, use its current context; otherwise read the canonical GitHub documents linked there. Report inaccessible sources rather than guessing.

After meaningful progress, update this context plus canonical Resolve context and Harry's plan with task IDs, verification, commit when available, remaining work and blockers. Mark complete only after implementation and verification; unit tests do not replace live Voice qualification. If the canonical checkout is unavailable, record the required synchronization here and leave that task open.

Keep Voice external: core -> Hutch adapter -> Resolve. Account investigation, authorization, confirmation policy, provider actions, cases and receipts belong to Resolve. Coordinate wire-contract changes across both repositories. Preserve unrelated changes and never copy secrets, raw audio or real customer data into source control.

# Repository operating rules

- Perform repository changes on dedicated feature branches created from `develop`.
- Merge completed feature branches into `develop` for integration and user testing.
- Promote `develop` to `main` only after the user confirms testing succeeded and explicitly approves the promotion.
- Never make implementation changes directly on `main` or `develop`.
- Never push commits or branches to `origin` (or any other remote) unless the user explicitly authorizes that specific push.
- Local commits, merges, and worktrees do not imply permission to push.

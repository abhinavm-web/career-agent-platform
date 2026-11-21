# Product review: usability and drift (November 2026)

**Method:** Ran the dashboard with fictional data (62 saved roles, plus an empty workspace for first-run), drove every top-level screen in Chromium at 1440×900 and 390×844, and measured page heights, scroll containers and visible controls. Code references are to `src/career_agent/web/`. Items marked *not verified* were not exercised.

## Verdict

The product's *substance* is strong and consistent with the PRD: evidence-first matching, truthful drafting, manual approval boundaries. The *experience* has drifted into a dense expert tool. Each of the 157 merged slices added correct, well-tested capability, but nobody owned the whole-screen result. The endless scrolling is the clearest symptom, not the only one.

## The endless-scrolling problem

Three independent causes, all of which pushed the page taller than any screen should be:

| Screen (desktop, 60+ roles) | Before | After this change |
| --- | --- | --- |
| Opportunity board, role selected | 5,335 px | 1,226 px |
| Opportunity board, nothing selected | 2,564 px | 1,725 px |
| Application tracker | 8,378 px | 1,783 px |
| H-1B research | 14,035 px (19,348 on mobile) | **unchanged, see P0-1** |

1. **Contradictory CSS layers.** `.job-list` was bounded at `app.css:92` (`max-height:720px; overflow-y:auto`) and then un-bounded at `app.css:256` (`max-height:none`). Meanwhile `.detail-panel` was `sticky` with its own `overflow:auto`. Result: a list that grows forever beside a detail pane clipped to the viewport. 96 of 562 selectors are defined two or more times; this was bound to happen.
2. **Cards that carry the whole case file.** Every role card embedded 8 evidence fields (≈260–290 px), repeating what the detail pane already shows.
3. **Tracker and H-1B render everything.** The tracker listed all 62 roles as "discovered" in one 7,000 px column with its detail pane far above the selected row. H-1B renders every role with a full employer record inline.

**Fixed here (board and tracker):** cards keep the two triage signals (verified evidence, requirement to verify) and move the rest to assistive-technology-only text; on screens wider than 1050 px the list and detail scroll independently inside the viewport (master-detail); the tracker filter block is two columns so the list gets room. Regression test: `tests/test_board_scroll_browser.py` (fails on the previous code at 5,071 px, passes now).

## Prioritised issues

### P0: blocks real use

1. **H-1B research is 14,000 px of employer records.** Server paging is 25 jobs, but each job card embeds a full employer panel (about 530 px, 127 `<details>` elements on the screen). This needs a list-plus-detail redesign like the board, not a CSS patch. *Not fixed here.*
2. **Both headline capabilities need setup outside the app.** Job discovery requires the optional `jobspy` extra (not installed by default); AI drafting requires configuring a model route. The top calls to action ("Find jobs" appears in at least five places) lead to an error or setup step for a new install. *Live discovery not verified.*
3. **The profile screen gates everything and shows 35 controls at once.** Nothing can be drafted without verified facts, yet the screen presents four separate AI entry points ("Review and strengthen…", "Ask a career assistant", "Suggest structured facts with AI", "Suggest résumé facts"), repeated "Load model routes" buttons, import/export, and an answer bank. A first-time user has no single obvious next step.

### P1: slows people down

4. **Navigation mirrors the build history, not the user's journey.** Nine destinations. Core features (AI analysis, Saved searches, Review history) sit under "Tools & settings"; H-1B research is top-level although each role already has a Sponsorship tab. The same sponsorship evidence appears in three places.
5. **The tracker duplicates the board.** All 62 saved roles show as "discovered". A tracker should show roles you have acted on. Pipeline counts and follow-ups render *below* the list, and two paragraphs of caveats precede any content.
6. **Empty states shout.** "Unknown · No linked records", "Not checked" and "No recognized wording available" repeat on every card. An empty workspace shows pagination ("Page 1 of 1"), a bulk-archive button and three "find jobs" controls with zero jobs.
7. **Help-by-icon signals too much UI.** Every heading has an ⓘ plus a "Control help" mode. Useful, but it is compensating for density and vocabulary ("Historical filing entity", "Keyword 49/100").
8. **Mobile remains long** (board 3,417 px unselected) although no longer unbounded.

### P2: health, which causes the drift

9. **The PRD contradicts itself on the most sensitive boundary.** §5 lists "Application sending: **None**", while §2 and merged PRs #155/#156 describe supervised Lever submission through the browser companion. This needs an owner decision and a correct PRD before anything else ships near that boundary.
10. **Structural debt.** `app.js` is 3,488 lines; `dashboard.py` is 1,908. There was no layout-level test, which is how a 5,000 px page shipped. `tests/test_resume_ai_browser.py` is flaky on unmodified `HEAD` (1 failure in 4 runs).
11. **Existing failures on `HEAD`** (unchanged by this work): five browser/dashboard tests fail without the optional `[ai]` extra or Chromium setup, and `test_supervised_application_browser` hangs past 170 s in a network-restricted sandbox.

## Recommended sequence

1. **Now (done):** board and tracker scrolling plus regression test.
2. **Next slice:** H-1B as master-detail, with the employer record loading on selection (P0-1).
3. **Then:** a single "next best action" on Home that adapts to setup state (profile, jobs, AI) and an in-app setup check for JobSpy and model routes (P0-2, P0-3).
4. **Then:** collapse navigation to Search / Pipeline / Profile / Settings; fold H-1B into the role's Sponsorship tab with an optional research view (P1-4, P1-5).
5. **Ongoing:** one owner for CSS (remove superseded rules), and a layout budget test per screen.

## Decisions needed from the owner

- Is supervised Lever submission in scope? Reconcile PRD §2/§5 either way.
- Is H-1B research a destination or a facet of each role?
- Should the tracker show only roles the user has acted on?
- Who is the first-run user, and is a guided setup preferable to the current open dashboard?

## Status update: first overhaul slice

Done on this branch (owner decisions: H-1B is a role tab, the tracker shows only acted roles, questions are asked step by step):

- **Résumé tab per role** with the original résumé left and the recommended version right; additions are tagged and every bullet is a verified fact. A three-step wizard adds experience (confirm required) and can ask a model for a sharper question after explicit consent. See [Dashboard](DASHBOARD.md#résumé-tab-original-beside-recommended).
- **H-1B moved into each role's Sponsorship tab.** The all-roles list (P0-1) is removed; employer search, preferences and imports remain under Settings & data.
- **Tracker lists only roles you acted on**, with **Add to tracker** on each role. A role archived from the board that you never saved stays out of the tracker.
- **Dropdowns removed** where they hid primary content on a role: saved description, AI résumé panel, AI wording, fact picker, résumé and cover-note preview, and application answers. Rare edit tools (correct description, browser capture) stay collapsed because their unsaved-work guard depends on it.
- **Scrolling:** the list is a pinned navigator; the role detail flows with the page, so there are no nested scroll boxes on the Résumé tab.

Not done, and why:

- **AI cannot reword résumé bullets.** The existing wording endpoint deliberately restores each fact's verified text, so "Find more matches with AI" selects among verified facts only. True rewording needs a product and safety decision.
- **Profile, Saved searches, AI analysis and Settings screens** still use top-level dropdown panels. The profile screen's 35 controls (P0-3) are the next candidate for a wizard.
- **Home setup guidance, JobSpy and model-route setup checks (P0-2)** are unchanged.
- **Overlap to resolve:** the Application tab still has the older "AI resume and cover letter", "Compare AI wording" and "Choose facts" panels, which now duplicate the Résumé tab. They remain because the reviewed export path depends on them.

## Status update: the simple app

After the first overhaul slice the owner asked for something a high-school graduate can use without help. The default page (`/`) is now a separate, small app with three places (Jobs, My résumé, More tools). The classic workspace, including the earlier Résumé tab, moved to `/classic` and remains available. Choices made with the owner: two paper pages side by side with new lines highlighted and Word or print-to-PDF output; works without AI and improves with it; the rest of the app hidden behind **More tools**, not deleted; a quick yes/no per résumé line to confirm it is true.

Not in the simple app (deliberately, still in `/classic`): application tracking, sponsorship evidence, saved searches, AI requirement analysis, the profile editor, and the reviewed-packet export path. The simple app does not record that you applied.

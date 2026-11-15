# Career workspace design system

This document describes the local workspace interface. The palette and components are a foundation for future commercial work; the current application remains a local prototype.

## Principles

- Help people move from finding a job to evaluating evidence and preparing an application.
- Distinguish facts from suggestions. Show uncertainty and source alongside sponsorship evidence.
- Keep controls understandable and keyboard accessible. Preserve unsaved work across navigation.
- Do not imply that historic filings establish present hiring or role-specific sponsorship.

## Visual language

The workspace uses a warm light canvas (`--bg`), white surfaces (`--panel`), ink text (`--text`), muted secondary text (`--muted`), and an indigo interaction accent (`--teal`, retained as a compatibility token). The sidebar is a separate dark surface. Amber, green and red are paired with descriptive text; color alone never communicates an outcome.

Use the existing `.panel`, `.button`, `.button-primary`, `.chip`, `.notice`, and `.detail-jump` classes. Controls must have a visible focus state, useful accessible names, and loading, error, empty and disabled states. Forms need explicit labels. Avoid relying on placeholder text as a label.

## Opportunity workspace

The board combines a filterable list with one selected role. The overview's evidence summary separates posting language from historical employer filing records and links to the sponsorship tab. Employer-name matches are not a legal identity verification. The score is identified as a keyword score and is not a hiring prediction. Preparing an application opens the existing review workflow; it does not submit anything.

At narrow widths, navigation scrolls horizontally with a visible scroll affordance, list and detail stack, and evidence tiles become a single column. No content should require page-level horizontal scrolling at 320px. Honor reduced motion. Keep keyboard tab order and tab-panel semantics intact.

## Implementation status

The shared visual system, job evidence summary, discovery shortcut, tracker list and grouped board, and responsive styles are implemented on the existing local workspace. A dedicated document-editing studio, drag-to-move pipeline, hosted accounts and billing remain future work; the interface must not imply that they already exist.

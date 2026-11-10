# Candidate intelligence and human-review workflow

## Goal and scope

Produce a **draft only** from candidate-confirmed statements, rank job fit using explainable heuristics, attach H-1B evidence caveats, and require explicit human approval. This does not send an application or answer work-authorization screening questions.

## Local CLI

Install with the optional real LangGraph dependency:

    python -m pip install -e ".[graph]"

Seed fictional demo data:

    career-agent ingest examples/jobs.sample.csv
    career-agent list

Choose a \`job_id\` displayed by the listing. Generate a packet:

    career-agent draft JOB_ID --profile examples/profile.example.json --facts examples/candidate-facts.example.json

The packet contains the supplied facts verbatim (not paraphrased), a suggested cover note, skill-fit evidence, sponsor status, candidate questions and a SHA-256 fingerprint. By default the JSON is written under \`.local/packets/\`, outside Git tracking.

Review a saved packet:

    career-agent review .local/packets/JOB_ID.json

The CLI prints the complete draft and job details. Type \`APPROVE JOB_ID\` exactly to record *approval for manual submission*. Any other input rejects it. **No submission occurs.**

End-to-end graph workflow:

    career-agent workflow JOB_ID --profile examples/profile.example.json --facts examples/candidate-facts.example.json

The LangGraph pipeline executes \`analyze → draft → human_review\`. The human-review node uses an actual \`interrupt()\` and resumes with \`Command(resume=...)\`. It checks a packet fingerprint so an earlier approval cannot accidentally apply to a different version.

The current graph uses \`InMemorySaver\`, so an interrupted run does not survive a process restart. The CLI conducts the review in one process. Production will need authenticated reviewers, durable checkpoints, tamper-resistant audit logs and private encrypted storage.

## Interpreting the skill comparison

`candidate_skills_not_mentioned_in_posting` lists skills that the **candidate already reports** but that are not mentioned in the job posting. It does **not** identify skills the job requires that the candidate lacks. `qualification_gaps_assessed` is always `false` in this heuristic MVP. For example, a Python-skilled candidate viewing a Rust role may see Python in this list, but must **not** interpret Python as a missing qualification.

Application packets now use schema version 2; older saved version 1 packets must be regenerated and reviewed again. Scores and rank order are unchanged.

## Fact bank

A factual statement requires \`id\`, \`section\`, \`statement\`, \`keywords\`, \`source_note\`, and \`verified_by_candidate: true\`. Unverified facts and duplicate IDs fail validation. \`source_note\` remains in the local fact bank, not in the generated application packet.

Allowed sections: \`experience\`, \`projects\`, \`skills\`, \`education\`, \`certifications\`.

The draft copies statements exactly and ranks them using keyword overlap. It does **not** enhance metrics, invent employment periods, claim qualifications the candidate has not verified, or decide legal eligibility.

## Security limitations

This is local experimental software, not a production SaaS. Candidate source notes and drafts must remain in private, untracked storage. A SHA-256 fingerprint detects accidental packet modification but is not a digital signature or permission system. No logging, cloud persistence, ATS account connection, or automated external submission is built.

See [Sponsorship methodology](SPONSORSHIP.md) for limitations of employer filing data.

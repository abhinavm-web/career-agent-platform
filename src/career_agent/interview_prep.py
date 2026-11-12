"""Local, source-linked prompts for candidate-led interview preparation."""


def preparation_guide(evidence):
    """Turn an already-scoped evidence snapshot into bounded prompts; infer no answers."""
    requirements = evidence['requirements']
    prompts = []
    for row in requirements[:8]:
        matches = row['suggested_facts']
        prompts.append({
            'question': ('Which example from your experience could you discuss in relation to this posting excerpt?'
                         if matches else 'What would you ask the employer to clarify about this posting excerpt?'),
            'source_heading': row['source_heading'], 'quote': row['quote'], 'category': row['category'],
            'suggested_facts': matches[:2], 'additional_fact_matches': max(0, len(matches) - 2),
        })
    return {
        'job_id': evidence['job_id'], 'description_revision': evidence['description_revision'],
        'workspace_revision': evidence['workspace_revision'], 'prompts': prompts,
        'truncated': evidence['truncated'] or len(requirements) > 8,
        'general_questions': [
            'Which verified examples best show how you approached work relevant to this role?',
            'What would you like to clarify about the role, team and measures of success?',
            'Which practical details need confirmation before you decide whether to proceed?',
        ],
        'notice': ('Preparation prompts only, not actual employer interview questions. '
                   'Keyword links are suggestions, not proof of fit; use only examples you can substantiate. '
                   'The saved posting and profile may change; regenerate after updates.'),
    }

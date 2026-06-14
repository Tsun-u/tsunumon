"""Mock LLM — 回傳 hardcoded 教學腳本，用於開發測試 pipeline。"""

from app.models.domain import (
    CourseOutline,
    PersonaAnalysis,
    ScriptSegment,
    SequenceOutline,
    TeachingRequest,
    TeachingScript,
)
from app.services.llm.base import BaseLLMService


class MockLLMService(BaseLLMService):
    async def generate_outline(self, request: TeachingRequest, outline_model: str | None = None, **kwargs) -> CourseOutline:
        return CourseOutline(
            course_topic=request.course_requirement,
            target_audience=request.student_persona,
            learning_objectives=[
                f"Understand the basics of {request.course_requirement}",
                "Apply concepts to real-world examples",
            ],
            scaffolding_strategy="hook -> core concept -> example -> summary",
            persona_analysis=PersonaAnalysis(
                prior_knowledge="Basic understanding assumed",
                knowledge_gaps="Core concepts need introduction",
                depth_decisions="Focus on fundamentals, skip advanced topics",
            ),
            sequences=[
                SequenceOutline(
                    sequence_id=1, title="Introduction",
                    teaching_phase="hook", objectives=["Grab attention"],
                    key_concepts=["Topic overview"], visual_strategy="title",
                    estimated_duration_min=0.5,
                ),
                SequenceOutline(
                    sequence_id=2, title="Core Concept",
                    teaching_phase="core", objectives=["Understand main ideas"],
                    key_concepts=["Key idea 1", "Key idea 2", "Key idea 3"],
                    visual_strategy="bullets", estimated_duration_min=0.5,
                ),
                SequenceOutline(
                    sequence_id=3, title="Example",
                    teaching_phase="example", objectives=["Apply concepts"],
                    key_concepts=["Real world application"],
                    visual_strategy="diagram", estimated_duration_min=0.5,
                ),
                SequenceOutline(
                    sequence_id=4, title="Summary",
                    teaching_phase="summary", objectives=["Recap key takeaways"],
                    key_concepts=["Takeaway 1", "Takeaway 2"],
                    visual_strategy="bullets", estimated_duration_min=0.5,
                ),
            ],
        )

    async def expand_outline(self, request: TeachingRequest, outline: CourseOutline, **kwargs) -> TeachingScript:
        topic = request.course_requirement
        segments = [
            ScriptSegment(
                segment_id=1, sequence_id=1,
                slide_title="Introduction",
                slide_html=(
                    '<div class="slide phase-hook">'
                    '<div class="phase-badge">\U0001f3af Hook</div>'
                    f'<div class="title">Welcome to {topic}</div>'
                    '<div class="divider"></div>'
                    '<ul class="bullet-list">'
                    '<li>What is this topic?</li>'
                    '<li>Why does it matter?</li>'
                    '</ul>'
                    '<span class="page-num">1</span>'
                    '</div>'
                ),
                narration_text=(
                    f"Welcome! Today we will explore {topic}. "
                    "This is an important topic that forms the foundation of many concepts."
                ),
                estimated_duration_sec=15.0,
                teaching_phase="hook",
            ),
            ScriptSegment(
                segment_id=2, sequence_id=2,
                slide_title="Core Concept",
                slide_html=(
                    '<div class="slide phase-core">'
                    '<div class="phase-badge">\u2728 Core Concepts</div>'
                    '<div class="title">Core Concept</div>'
                    '<div class="divider"></div>'
                    '<div style="display:flex;gap:20px">'
                    '<div class="card" style="flex:1;border-color:#a5d6a7">'
                    '<span class="card__num" style="background:#66bb6a">1</span>'
                    '<div class="card__title" style="color:#2e7d32">Key Idea One</div>'
                    '<div class="card__text">Fundamental to understanding the rest.</div>'
                    '</div>'
                    '<div class="card" style="flex:1;border-color:#90caf9">'
                    '<span class="card__num" style="background:#42a5f5">2</span>'
                    '<div class="card__title" style="color:#1565c0">Key Idea Two</div>'
                    '<div class="card__text">Builds upon the first concept.</div>'
                    '</div>'
                    '<div class="card" style="flex:1;border-color:#ce93d8">'
                    '<span class="card__num" style="background:#ab47bc">3</span>'
                    '<div class="card__title" style="color:#6a1b9a">Key Idea Three</div>'
                    '<div class="card__text">Ties everything together.</div>'
                    '</div>'
                    '</div>'
                    '<span class="page-num">2</span>'
                    '</div>'
                ),
                narration_text=(
                    "Let's dive into the core concept. "
                    "The first key idea is fundamental to understanding the rest. "
                    "The second builds upon it, and the third ties everything together."
                ),
                estimated_duration_sec=20.0,
                teaching_phase="core",
            ),
            ScriptSegment(
                segment_id=3, sequence_id=3,
                slide_title="Example",
                slide_html=(
                    '<div class="slide phase-example">'
                    '<div class="phase-badge">\U0001f4dd Worked Example</div>'
                    '<div class="title">Real World Application</div>'
                    '<div class="divider"></div>'
                    '<ul class="bullet-list">'
                    '<li><strong>Step 1:</strong> Identify the problem</li>'
                    '<li><strong>Step 2:</strong> Apply what we learned</li>'
                    '<li><strong>Step 3:</strong> Verify the result</li>'
                    '</ul>'
                    '<span class="page-num">3</span>'
                    '</div>'
                ),
                narration_text=(
                    "Now let's look at a real world example. "
                    "Imagine you are solving this step by step. "
                    "First, you identify the problem. Then, you apply what we just learned."
                ),
                estimated_duration_sec=20.0,
                teaching_phase="example",
            ),
            ScriptSegment(
                segment_id=4, sequence_id=4,
                slide_title="Summary",
                slide_html=(
                    '<div class="slide phase-summary">'
                    '<div class="phase-badge">\u2705 Summary</div>'
                    '<div class="title">Key Takeaways</div>'
                    '<div class="divider"></div>'
                    '<ul class="bullet-list">'
                    '<li>Key takeaway one</li>'
                    '<li>Key takeaway two</li>'
                    '<li>What to explore next</li>'
                    '</ul>'
                    '<span class="page-num">4</span>'
                    '</div>'
                ),
                narration_text=(
                    "Let's recap what we learned today. "
                    "The most important takeaway is the core concept and how it applies "
                    "in practice. I encourage you to explore further on your own. "
                    "Thanks for watching!"
                ),
                estimated_duration_sec=15.0,
                teaching_phase="summary",
            ),
        ]

        return TeachingScript(
            request_id=request.request_id,
            title=f"Introduction to {request.course_requirement}",
            outline=outline,
            segments=segments,
            scaffolding_strategy=outline.scaffolding_strategy,
            target_duration_min=2,
        )

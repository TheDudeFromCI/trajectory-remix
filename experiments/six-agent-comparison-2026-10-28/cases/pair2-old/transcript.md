# pair2-old: library-survey-tradeoff-1

Pipeline status: `evaluation_failed`. No Ready approval.

## Original scenario

{
  "problem": "Help me choose one resident survey plan for our library's proposed Sunday opening, explain the accuracy tradeoff in plain language, and draft a short recommendation for the committee. I want a reliable answer and a small margin of error, but I keep mixing up sample size with whether the sample is fair.",
  "situation": "I am a volunteer with no statistics training and tend to jump between the budget and publicity details. The authoritative plan options and constraints are in /survey-options.txt. I have not collected any responses. Please keep me focused on choosing a plan; I also keep wondering whether a larger survey automatically proves what most residents want.",
  "success_criteria": "Recommend the random resident sample as the better basis for describing all adult residents, while explicitly acknowledging that it costs more and has a larger nominal margin than the volunteer poll. Using the supplied approximation, calculate approximately ±8.3 percentage points for 144 responses and ±5 points for 400 responses; explain that the volunteer margin does not account for self-selection bias. Show that achieving ±5 points with random sampling would require 400 completed responses and cost $1,600, exceeding the $600 cap. Explain that neither plan guarantees correctness or proves a majority before responses exist. Provide a concise committee recommendation consistent with the user's reliability-first preference, without claiming that either plan meets every goal.",
  "files": [
    {
      "path": "/survey-options.txt",
      "content": "Decision: choose exactly one plan to estimate the percentage of all adult residents who support Sunday library opening. No survey results exist yet. Priority: represent all adult residents fairly; secondary goal: nominal 95% margin of error no greater than ±5 percentage points. Hard spending cap: $600. Plan R: 144 completed responses selected randomly from a complete adult-resident register, $4 per completed response, total $576. Assume every selected resident responds and each resident has equal selection probability. Plan V: 400 completed responses from a public social-media link, flat total cost $100. People choose whether to participate; their opinions may differ from other residents. Both plans finish by the committee deadline. The population is large enough to ignore finite-population corrections. For this exercise, use conservative approximate 95% margin of error in percentage points = 100/sqrt(n), where n is the number of completed responses. This approximation describes random sampling error, not selection bias. Random sampling can be expanded only at the same $4-per-response rate, but the two listed plans are the available choices for this decision."
    }
  ],
  "identifier": "library-survey-tradeoff"
}

## Replay content and decisions

**User (tick 1)**

User: I'm helping our library decide how to survey adult residents about proposed Sunday opening. Could you read /survey-options.txt, choose one of the two plans, explain the accuracy tradeoff without statistics jargon, and give me a short recommendation I can take to the committee? Our first priority is representing all adult residents fairly, with a hard $600 spending cap; we'd also like a 95% margin of error of no more than ±5 percentage points. We haven't collected any responses. I keep getting distracted by the cheaper social-media option—400 answers sounds more convincing than 144, but does bigger automatically mean fairer or prove what most people want? Sorry, I'm also thinking about publicity; please keep me focused on choosing the survey plan. The file has the costs, assumptions, and the margin-of-error formula we're supposed to use.

**Generated tool request (tick 2)**

```json
{"tool": "file_read", "arguments": {"path": "/survey-options.txt"}}
```

**Private notes: reasoning-1 (tick 36)**

Evidence: file_read succeeded for /survey-options.txt, version sha256:e86dea7f40cfcc58594832c7bbfcf62b9f589d19960796e680c1f6b8d368af3e. Goal is choose exactly one plan to estimate all adult residents' support for Sunday opening. Primary priority: fair representation; secondary goal: nominal 95% margin ≤±5 percentage points; hard cap $600. No responses exist. R: 144 completed random responses from complete adult register, equal selection probability and every selected resident responds; $4 each, total $576. V: 400 self-selected public social-media respondents, flat $100; their opinions may differ from other residents. Both meet deadline. Large population; ignore finite-population correction. Supplied approximation is 100/sqrt(n) percentage points, describing random sampling error only, not selection bias. Only the two listed plans are available; random expansion rate $4/response is relevant to explaining target infeasibility, not a third option. Arithmetic checks directly: sqrt(144)=12, margin 8.333 points; sqrt(400)=20, nominal margin 5 points. R leaves $24 and costs $476 more than V. For random margin≤5, sqrt(n)≥20 so n≥400, cost $1600, $1000 over cap. Recommend R given explicit reliability-first priority. Acknowledge wider random margin and higher cost, missing ±5 target. V's ±5 is just formula substitution, not a valid complete uncertainty bound for its volunteer design; larger n cannot remove self-selection bias. Explain distinction simply: size controls random variation, selection determines whose views are heard. Neither method guarantees truth; without results neither establishes majority support. Provide a brief committee-ready draft after concise explanation. No missing information or tools needed; do not invent findings or move to publicity.

**Delivered assistant reply (request starts tick 682)**

Choose Plan R: 144 randomly selected adult residents for $576, leaving $24 of the $600 cap. I read /survey-options.txt; R best matches your first priority of fair representation. Bigger does not automatically mean fairer: a larger random sample reduces chance fluctuations, but a social-media poll may attract people with particular opinions. More volunteers cannot remove that selection problem. Using the supplied formula, 100/sqrt(n), R has an approximate 95% sampling margin of ±8.3 percentage points (100/12). Plan V costs $100 for 400 volunteer responses; substituting 400 gives ±5 points (100/20), but that nominal figure does not account for self-selection and should not be treated as a reliable margin for all adult residents. The compromise is that R costs $476 more and has a wider sampling margin, missing your ±5-point target. Meeting that target through random sampling would require 400 responses at $4 each: $1,600, or $1,000 over budget. Neither offered plan meets every goal. Neither guarantees correctness, and no responses exist yet, so neither proves majority support. Committee recommendation: Choose Plan R for a fairer estimate of adult residents' views within the $600 budget. Its $576 cost buys 144 random responses with an approximate 95% sampling margin of ±8.3 percentage points. The cheaper, larger volunteer poll risks skewed findings. A random survey meeting our ±5-point target would cost $1,600. We will assess support after collecting responses.

## Independent review

correct draft; presentation improvement advised

- Correctly prioritizes representative random sampling under the $600 cap over the larger self-selected poll.
- Independent checks: 100/sqrt(144)=8.333 percentage points; 100/sqrt(400)=5; random target costs 400×$4=$1600, $1000 over cap. R costs $576 and leaves $24.
- Properly distinguishes sampling variation from selection bias and does not invent survey results or claim a majority.
- The long single-paragraph answer and repeated committee wording are less easy to scan for a distracted novice. This is a presentation issue, not a wrong recommendation.

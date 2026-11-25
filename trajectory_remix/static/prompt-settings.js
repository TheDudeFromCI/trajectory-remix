/* Settings → Generation prompts: the agent and authoring prompts and the Draft, Assembly and Assistant instructions. Each is collapsible. */
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };

const PROMPTS = [
  ['actor', 'Agent prompt', 'What a trajectory is and how its agent behaves. It opens the system prompt of every endpoint: Draft, Assembly, Fast and Critical Review, and Assistant. Add a persona pack at the end.'],
  ['authoring', 'Authoring prompt', 'How to use the authoring tools. Assembly, review and Assistant sessions see it after the agent prompt.'],
  ['draft', 'Draft instructions', 'The Draft task: write plain-prose working notes for the whole episode from the sampled proposal.'],
  ['assembly', 'Assembly instructions', 'The Assembly task: build the trajectory from the Draft with the authoring tools.'],
  ['assistant', 'Assistant instructions', 'The Assistant task: opens each Send to agent message, before the comments table. Keep the instruction to answer each comment with the `reply` tool.'],
];

export function renderPromptSettings(target, {current, write, error, render, loadDefault, actions = null}) {
  const box = node('div', undefined, 'settings-limits');
  for (const [key, title, help] of PROMPTS) {
    const section = node('details', undefined, 'prompt-section'), text = node('textarea');
    section.open = true; section.dataset.prompt = key;
    text.value = current().prompts?.[key] ?? ''; text.rows = 8; text.setAttribute('aria-label', title);
    text.oninput = () => write(d => { d.prompts ||= {}; d.prompts[key] = text.value; });
    section.append(node('summary', title), node('p', help, 'settings-help'), text); box.append(section);
  }
  if (loadDefault) {
    const reset = node('button', 'Load Default Prompts'); reset.type = 'button';
    reset.onclick = async () => {
      let defaults;
      try { defaults = await loadDefault(); } catch (cause) { return error('Could not load the default prompts: ' + cause.message); }
      if (!confirm('Replace the agent and authoring prompts and the Draft, Assembly and Assistant instructions with the packaged defaults? Your edits in this draft, including any persona text, will be lost. Nothing is saved until you choose Save settings.')) return;
      write(d => { d.prompts = {...defaults}; }); render('Prompts reset to the defaults. Choose Save settings to keep them.');
    };
    (actions || box).append(reset);
  }
  target.append(box);
}

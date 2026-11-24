/* Settings → Generation prompts: the agent prompt (persona), the Draft and Assembly instructions and the Assistant prompt. Each is collapsible. */
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };

const PROMPTS = [
  ['actor', 'Agent prompt', 'How the agent behaves. Both Draft and Assembly see it; add a persona pack here.'],
  ['draft', 'Draft instructions', 'Draft writes plain-prose working notes for the whole episode.'],
  ['assembly', 'Assembly instructions', 'Assembly turns the notes into the compact JSON spec.'],
  ['assistant', 'Assistant prompt', 'Guides the Assistant endpoint when it answers reviewer comments (Send to agent). Generation never uses it.'],
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
      if (!confirm('Replace the agent prompt, the Draft and Assembly instructions and the Assistant prompt with the packaged defaults? Your edits in this draft, including any persona text, will be lost. Nothing is saved until you choose Save settings.')) return;
      write(d => { d.prompts = {...defaults}; }); render('Prompts reset to the defaults. Choose Save settings to keep them.');
    };
    (actions || box).append(reset);
  }
  target.append(box);
}

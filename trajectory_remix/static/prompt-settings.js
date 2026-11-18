/* Settings → Generation prompts: the agent prompt (persona) and the Draft and Assembly instructions. */
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };

const PROMPTS = [
  ['actor', 'Agent prompt', 'How the agent behaves. Both Draft and Assembly see it; add a persona pack here.'],
  ['draft', 'Draft instructions', 'Draft writes plain-prose working notes for the whole episode.'],
  ['assembly', 'Assembly instructions', 'Assembly turns the notes into the compact JSON spec.'],
];

export function renderPromptSettings(target, {current, write, error, render, loadDefault}) {
  const box = node('div', undefined, 'settings-limits'); box.append(node('h4', 'Generation prompts'), node('p', 'Runs freeze the prompts when they start; edits apply to new runs.', 'settings-help'));
  for (const [key, title, help] of PROMPTS) {
    const label = node('label', title), text = node('textarea');
    text.value = current().prompts?.[key] ?? ''; text.rows = 8; text.setAttribute('aria-label', title);
    text.oninput = () => write(d => { d.prompts ||= {}; d.prompts[key] = text.value; });
    label.append(text); box.append(label, node('p', help, 'settings-help'));
  }
  if (loadDefault) {
    const reset = node('button', 'Load default prompts'); reset.type = 'button';
    reset.onclick = async () => {
      let defaults;
      try { defaults = await loadDefault(); } catch (cause) { return error('Could not load the default prompts: ' + cause.message); }
      if (!confirm('Replace the agent prompt and the Draft and Assembly instructions with the packaged defaults? Your edits in this draft, including any persona text, will be lost. Nothing is saved until you choose Save settings.')) return;
      write(d => { d.prompts = {...defaults}; }); render('Prompts reset to the defaults. Choose Save settings to keep them.');
    };
    box.append(reset);
  }
  target.append(box);
}

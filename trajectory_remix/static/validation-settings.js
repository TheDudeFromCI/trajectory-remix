/* Project warning choices come from the same catalogue as compiler diagnostics. */
export function renderValidationSettings(container, choices, {getSettings, onChange}) {
  container.replaceChildren();
  const intro = document.createElement('p');
  intro.className = 'settings-help validation-intro';
  intro.textContent = 'Enabled warnings must be resolved before signoff. Errors are always checked.';
  container.append(intro);
  const group = document.createElement('div');
  group.className = 'validation-options';
  for (const choice of choices) {
    const label = document.createElement('label');
    const checkbox = document.createElement('input');
    const text = document.createElement('span');
    const name = document.createElement('strong');
    const description = document.createElement('span');
    const code = document.createElement('code');
    checkbox.type = 'checkbox';
    checkbox.checked = !(getSettings().disabled_warnings || []).includes(choice.code);
    checkbox.setAttribute('aria-label', choice.name);
    checkbox.addEventListener('change', () => {
      const settings = getSettings();
      const disabled = new Set(settings.disabled_warnings || []);
      if (checkbox.checked) disabled.delete(choice.code);
      else disabled.add(choice.code);
      settings.disabled_warnings = [...disabled].sort();
      onChange(settings);
    });
    name.textContent = choice.name;
    description.textContent = choice.description;
    code.textContent = choice.code;
    text.append(name, description, code);
    label.append(checkbox, text);
    group.append(label);
  }
  container.append(group);
}

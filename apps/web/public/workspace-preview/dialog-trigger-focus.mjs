let triggerSequence = 0;

document.addEventListener('click', event => {
  const trigger = event.target.closest('[data-action]');
  if (!trigger || trigger.id) return;
  trigger.id = `workspace-action-${++triggerSequence}`;
}, true);

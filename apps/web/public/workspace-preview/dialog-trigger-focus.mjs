let triggerSequence = 0;

document.addEventListener('click', event => {
  const trigger = event.target.closest('[data-action]');
  if (!trigger) return;
  if (!trigger.id) trigger.id = `workspace-action-${++triggerSequence}`;
  // Safari does not always focus pointer-clicked buttons. Make the trigger the
  // active element before workspace.mjs records document.activeElement.
  trigger.focus({ preventScroll: true });
}, true);

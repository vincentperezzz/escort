export function requiresProjectSetup(status) {
  if (!status) return false;
  if (status.needs_project === true) return true;
  const projects = status.projects;
  if (Array.isArray(projects) && projects.length === 0) return true;
  return false;
}

export function projectList(status) {
  return Array.isArray(status?.projects) ? status.projects : [];
}

export function activeProject(status) {
  return status?.active_project ?? null;
}

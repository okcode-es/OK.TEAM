export function normalizeDepartment(department?: string | null): string {
  // Match trimmed, case-insensitive department names, including Spanish accents.
  return (department ?? '').trim().toLowerCase()
}

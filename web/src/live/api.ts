export const API_ORIGIN = String(import.meta.env?.VITE_THESIS_API ?? '').replace(/\/+$/, '');
export const apiUrl = (path: string) => API_ORIGIN + path;

// Manejo de tokens JWT en localStorage

const TOKEN_KEY = 'telerin_token';
const USERNAME_KEY = 'telerin_username';

export const saveToken = (token: string, username: string) => {
  if (typeof window === 'undefined') return;
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USERNAME_KEY, username);
};

export const getToken = (): string | null => {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEY);
};

export const getUsername = (): string | null => {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(USERNAME_KEY);
};

export const clearToken = () => {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USERNAME_KEY);
};

export const isAuthenticated = (): boolean => {
  const token = getToken();
  if (!token) return false;
  try {
    // Decodificar payload (sin verificar firma — la verificación es serverside)
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.exp * 1000 > Date.now();
  } catch {
    return false;
  }
};

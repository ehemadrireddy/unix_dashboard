import axios from 'axios';

export const api = axios.create({
  baseURL: '/api', // FastAPI handles the proxy
  headers: { 'Content-Type': 'application/json' }
});

api.interceptors.request.use((req) => {
  const token = localStorage.getItem('token');
  if (token) req.headers.Authorization = `Bearer ${token}`;
  return req;
});
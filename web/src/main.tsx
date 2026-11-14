import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './app';
import { LiveDataProvider } from './live/provider';
import './styles/global.css';
import './styles/layout.css';

createRoot(document.getElementById('root')!).render(<StrictMode><LiveDataProvider><App /></LiveDataProvider></StrictMode>);

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import SaaSAuthGate from './components/SaaSAuthGate.tsx';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SaaSAuthGate>
      <App />
    </SaaSAuthGate>
  </StrictMode>,
);

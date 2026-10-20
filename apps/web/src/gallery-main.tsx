import { createRoot } from 'react-dom/client';
import { Gallery } from '#src/Gallery.tsx';
import '#src/styles.css';

createRoot(document.getElementById('root')!).render(<Gallery />);

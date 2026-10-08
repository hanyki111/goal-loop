import { Component, type ErrorInfo, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import '@xyflow/react/dist/style.css';
import './style.css';
import './workspace.css';
import { Workspace } from './Workspace';

class WorkspaceErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null } as { error: Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('Goal Loop Workspace rendering error', error, info); }
  render() {
    if (!this.state.error) return this.props.children;
    return <main className="workspace loading"><section className="load-error" role="alert"><h1>이 화면을 표시하지 못했습니다.</h1><p>기록 데이터는 변경되지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.</p><pre>{this.state.error.message}</pre><button onClick={() => window.location.reload()}>Workspace 다시 열기</button></section></main>;
  }
}

createRoot(document.getElementById('root')!).render(<WorkspaceErrorBoundary><Workspace /></WorkspaceErrorBoundary>);

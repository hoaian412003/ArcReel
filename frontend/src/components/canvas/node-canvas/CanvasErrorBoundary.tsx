import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

function Fallback({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <div role="alert" className="flex h-full w-full flex-col items-center justify-center gap-3 p-8 text-center">
      <h2 className="text-[15px] font-semibold text-text">{t("node_canvas_crash_title")}</h2>
      <p className="max-w-xl text-[12.5px] text-text-3">{t("node_canvas_crash_hint")}</p>
      <pre className="max-h-40 max-w-xl overflow-auto whitespace-pre-wrap rounded-[8px] bg-black/30 p-3 text-left text-[11px] text-red-300">
        {error.message}
      </pre>
      <button type="button" className={GHOST_BTN_CLS} onClick={onRetry}>
        {t("node_canvas_crash_retry")}
      </button>
    </div>
  );
}

/** 画布渲染出错时不白屏：显示错误信息和「重新加载画布」，并把完整堆栈打到控制台，方便定位。 */
export class CanvasErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[canvas] render crashed", error, info.componentStack);
  }

  render(): ReactNode {
    return this.state.error ? (
      <Fallback error={this.state.error} onRetry={() => this.setState({ error: null })} />
    ) : (
      this.props.children
    );
  }
}

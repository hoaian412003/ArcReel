import { useEffect, useMemo, useState } from "react";
import { useLocation } from "wouter";
import { projectCanvasPath } from "@/app-routes";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Columns3,
  Workflow,
  LayoutDashboard,
  BookOpen,
  Users,
  Landmark,
  Package,
  Plus,
  Scissors,
  Search,
  ShoppingBag,
} from "lucide-react";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { useAppStore } from "@/stores/app-store";
import { API } from "@/api";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { isDemoProject } from "@/onboarding/demo-project";
import { normalizeRoute } from "@/utils/generation-mode";
import { EpisodeCard } from "./EpisodeCard";
import { EpisodeSourceDialog } from "./EpisodeSourceDialog";
import { NovelSplitDialog } from "./NovelSplitDialog";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { deleteEpisode } from "@/actions/assets";
import { useEpisodeVideoExport } from "./use-episode-video-export";

interface AssetSidebarProps {
  className?: string;
}

interface NavItem {
  key: string;
  path: string;
  label: string;
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  meta?: number;
  /** 在新标签页里打开的页面（创作画布）：不走工作台路由，也没有「当前所在」的高亮。 */
  external?: string;
}

/**
 * 工作台侧栏 v3：
 * - 工作区导航（5 个胶囊按钮：项目概览 / 源文件 / 角色集 / 场景库 / 道具库）
 * - 分集列表（搜索 + 卡片列表，每张卡片含缩略+状态+进度+费用）
 * - 折叠态（64px）：仅图标 + Ex 字符
 */
export function AssetSidebar({ className }: AssetSidebarProps) {
  const { t } = useTranslation(["common", "dashboard"]);
  const { currentProjectName, currentProjectData } = useProjectsStore();
  const debouncedFetchCost = useCostStore((s) => s.debouncedFetch);
  const [location, setLocation] = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [search, setSearch] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [splitOpen, setSplitOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { exportEpisodeVideo, exporting } = useEpisodeVideoExport(currentProjectName);

  const characterCount = Object.keys(currentProjectData?.characters ?? {}).length;
  const sceneCount = Object.keys(currentProjectData?.scenes ?? {}).length;
  const propCount = Object.keys(currentProjectData?.props ?? {}).length;
  const productCount = Object.keys(currentProjectData?.products ?? {}).length;
  const episodes = currentProjectData?.episodes ?? [];
  // 广告/短片项目恒单集：隐藏「集」语义（标题/计数/搜索/添加），直达唯一视频
  const isAd = currentProjectData?.content_mode === "ad";

  const sourceFilesVersion = useAppStore((s) => s.sourceFilesVersion);
  const [sourceCount, setSourceCount] = useState<number>(0);

  // 演示项目没有服务端侧数据，源文件计数跳过（导航与分集列表照常渲染）
  const demoMode = useDemoWorkbench();
  // drama 不经分集规划：分集由用户逐集上传源文创建
  const canAddEpisode = currentProjectData?.content_mode === "drama" && !demoMode;

  useEffect(() => {
    if (currentProjectName) debouncedFetchCost(currentProjectName);
  }, [currentProjectName, debouncedFetchCost]);

  useEffect(() => {
    // demoMode 演示→真实切换时先于 store 变为 false，currentProjectName 单独判一次
    // 兜住这一帧仍读到旧演示项目名的窗口，避免对不存在的演示项目发一次必然失败的请求。
    if (!currentProjectName || demoMode || isDemoProject(currentProjectName)) return;
    let cancelled = false;
    API.listFiles(currentProjectName)
      .then((res) => {
        if (!cancelled) setSourceCount(res.files?.source?.length ?? 0);
      })
      .catch(() => {
        // 失败时保留上一份成功值，避免把网络/权限错误伪装成 0
      });
    return () => {
      cancelled = true;
    };
  }, [currentProjectName, sourceFilesVersion, demoMode]);

  // Derive active episode from `/episodes/:id`
  const activeEp = useMemo(() => {
    const m = location.match(/^\/episodes\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  }, [location]);

  const navItems: NavItem[] = [
    { key: "overview", path: "/", label: t("dashboard:workspace_nav_overview"), icon: LayoutDashboard },
    // 演示项目没有后端数据，Flow 看板与源文件入口一并隐藏，而非渲染必然报错的空页
    ...(demoMode
      ? []
      : [
          { key: "reviews", path: "/reviews", label: t("dashboard:review_inbox_nav"), icon: Columns3 },
          {
            key: "node-canvas",
            path: "/canvas",
            external: currentProjectName ? projectCanvasPath(currentProjectName) : undefined,
            label: t("dashboard:node_canvas_nav"),
            icon: Workflow,
          },
          {
            key: "characters",
            path: "/canvas",
            external: currentProjectName ? projectCanvasPath(currentProjectName, "characters") : undefined,
            label: t("dashboard:workspace_nav_characters"),
            icon: Users,
            meta: characterCount,
          },
          {
            key: "source",
            path: "/source",
            label: t("dashboard:workspace_nav_source"),
            icon: BookOpen,
            meta: sourceCount,
          },
        ]),
    // 真实项目的角色在创作画布里管理；演示项目没有画布，引导流程仍走只读的角色页。
    ...(demoMode
      ? [
          {
            key: "characters",
            path: "/characters",
            label: t("dashboard:workspace_nav_characters"),
            icon: Users,
            meta: characterCount,
          },
        ]
      : []),
    {
      key: "scenes",
      path: "/scenes",
      label: t("dashboard:workspace_nav_scenes"),
      icon: Landmark,
      meta: sceneCount,
    },
    {
      key: "props",
      path: "/props",
      label: t("dashboard:workspace_nav_props"),
      icon: Package,
      meta: propCount,
    },
    // 商品资产仅广告/短片项目使用（v1 单商品设定），其余模式隐藏入口
    ...(isAd
      ? [
          {
            key: "products",
            path: "/products",
            label: t("dashboard:workspace_nav_products"),
            icon: ShoppingBag,
            meta: productCount,
          },
        ]
      : []),
  ];

  const isNavActive = (item: NavItem): boolean => {
    if (item.path === "/") return location === "/";
    if (item.external) return false;
    return location === item.path || location.startsWith(item.path + "/");
  };

  // ad 隐藏搜索框，残留的 search state 不参与过滤，避免唯一视频入口被吞
  const filteredEps = isAd
    ? episodes
    : episodes.filter(
        (ep) => !search || ep.title.includes(search) || String(ep.episode).includes(search),
      );

  return (
    <aside
      className={`flex flex-col overflow-hidden ${className ?? ""}`}
      style={{
        width: collapsed ? 64 : 256,
        transition: "width .18s ease",
        borderRight: "1px solid var(--color-hairline)",
        background:
          "linear-gradient(180deg, oklch(0.195 0.011 265 / 0.6), oklch(0.175 0.010 265 / 0.5))",
        boxShadow: "inset -1px 0 0 oklch(1 0 0 / 0.015)",
      }}
    >
      {/* ---- Workspace nav ---- */}
      <div className="px-2.5 pb-1.5 pt-2.5">
        {navItems.map((item) => {
          const Icon = item.icon;
          const active = isNavActive(item);
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => (item.external ? window.open(item.external, "_blank", "noopener") : setLocation(item.path))}
              title={collapsed ? item.label : ""}
              aria-label={collapsed ? item.label : undefined}
              className="relative mb-px flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors focus-ring hover:bg-[oklch(0.26_0.012_265/0.5)]"
              style={{
                background: active
                  ? "linear-gradient(90deg, var(--color-accent-soft), var(--color-accent-dim) 70%, transparent)"
                  : "transparent",
                color: active ? "var(--color-text)" : "var(--color-text-2)",
              }}
            >
              {active && (
                <span
                  className="absolute -left-px top-[7px] bottom-[7px] w-0.5 rounded"
                  style={{
                    background: "var(--color-accent)",
                    boxShadow: "0 0 8px var(--color-accent-glow)",
                  }}
                />
              )}
              <span
                className="grid w-4 shrink-0 place-items-center"
                style={{ color: active ? "var(--color-accent-2)" : "var(--color-text-3)" }}
              >
                <Icon className="h-4 w-4" />
              </span>
              {!collapsed && (
                <>
                  <span
                    className="flex-1 text-left text-[13px]"
                    style={{
                      fontWeight: active ? 600 : 500,
                      letterSpacing: "-0.05px",
                    }}
                  >
                    {item.label}
                  </span>
                  {item.meta != null && (
                    <span
                      className="num rounded-[3px] px-1.5 py-px text-[10.5px]"
                      style={{
                        color: active ? "var(--color-text-3)" : "var(--color-text-4)",
                        background: active ? "oklch(0 0 0 / 0.2)" : "transparent",
                      }}
                    >
                      {item.meta}
                    </span>
                  )}
                </>
              )}
            </button>
          );
        })}
      </div>

      <div
        className="mx-3.5 my-1 h-px"
        style={{ background: "var(--color-hairline-soft)" }}
      />

      {/* ---- Episodes ---- */}
      {!collapsed ? (
        <>
          <div className="flex items-center gap-2 px-3.5 pb-1.5 pt-2.5">
            <span
              className="text-[10.5px] font-bold uppercase"
              style={{ color: "var(--color-text-4)", letterSpacing: "0.8px" }}
            >
              {isAd
                ? t("dashboard:ad_video_section_title")
                : t("dashboard:episodes_section_title")}
            </span>
            {!isAd && (
              <>
                <span className="num text-[10px]" style={{ color: "var(--color-text-4)" }}>
                  {episodes.length}
                </span>
                <span className="flex-1" />
                {canAddEpisode ? (
                  <button
                    type="button"
                    onClick={() => setSplitOpen(true)}
                    className="grid h-5 w-5 place-items-center rounded focus-ring"
                    style={{ background: "oklch(0.28 0.012 250 / 0.6)", color: "var(--color-text-3)" }}
                    title={t("dashboard:novel_split_button")}
                    aria-label={t("dashboard:novel_split_button")}
                  >
                    <Scissors className="h-3 w-3" />
                  </button>
                ) : null}
                <button
                  type="button"
                  disabled={!canAddEpisode}
                  aria-disabled={!canAddEpisode}
                  onClick={canAddEpisode ? () => setUploadOpen(true) : undefined}
                  className="grid h-5 w-5 place-items-center rounded focus-ring disabled:cursor-not-allowed disabled:opacity-50"
                  style={{
                    background: "oklch(0.28 0.012 250 / 0.6)",
                    color: "var(--color-text-3)",
                  }}
                  title={canAddEpisode ? t("dashboard:add_episode") : t("dashboard:add_episode_unavailable")}
                  aria-label={t("dashboard:add_episode")}
                >
                  <Plus className="h-3 w-3" />
                </button>
              </>
            )}
          </div>

          {!isAd && (
            <div className="px-2.5 pb-2">
              <div
                className="flex items-center gap-1.5 rounded-md px-2 py-1.5"
                style={{
                  background: "oklch(0.16 0.010 250 / 0.6)",
                  border: "1px solid var(--color-hairline)",
                }}
              >
                <Search
                  className="h-3 w-3 shrink-0"
                  style={{ color: "var(--color-text-4)" }}
                />
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t("dashboard:episode_search_placeholder")}
                  aria-label={t("dashboard:episode_search_placeholder")}
                  className="min-w-0 flex-1 bg-transparent text-xs outline-none focus-ring"
                  style={{ color: "var(--color-text)" }}
                />
              </div>
            </div>
          )}

          <div className="flex-1 overflow-y-auto px-2 pb-2.5">
            {filteredEps.length === 0 ? (
              <div
                className="px-2 py-6 text-center text-[11px] italic"
                style={{ color: "var(--color-text-4)" }}
              >
                {episodes.length === 0
                  ? t("dashboard:no_episodes_yet")
                  : t("dashboard:no_episode_search_results")}
              </div>
            ) : (
              filteredEps.map((ep) => (
                <EpisodeCard
                  key={ep.episode}
                  ep={ep}
                  active={ep.episode === activeEp}
                  onClick={() => setLocation(`/episodes/${ep.episode}`)}
                  showEpisodeBadge={!isAd}
                  fallbackTitle={isAd ? currentProjectData?.title : undefined}
                  route={normalizeRoute(currentProjectData?.generation_mode)}
                  onExportVideo={() => void exportEpisodeVideo(ep.episode)}
                  exportingVideo={exporting.has(ep.episode)}
                  onDelete={canAddEpisode ? () => setDeleteTarget(ep.episode) : undefined}
                />
              ))
            )}
          </div>
        </>
      ) : (
        <div className="flex-1 overflow-y-auto px-2.5 py-1.5">
          {filteredEps.map((ep) => {
            const epLabel = isAd
              ? t("dashboard:ad_video_section_title")
              : t("dashboard:episode_collapsed_button_label", {
                  episode: ep.episode,
                  title: ep.title,
                });
            return (
            <button
              key={ep.episode}
              type="button"
              onClick={() => setLocation(`/episodes/${ep.episode}`)}
              title={epLabel}
              aria-label={epLabel}
              className="num mb-[3px] flex h-9 w-full items-center justify-center rounded-md text-[11px] font-bold focus-ring"
              style={{
                background: ep.episode === activeEp ? "var(--color-accent-dim)" : "transparent",
                color:
                  ep.episode === activeEp
                    ? "var(--color-accent-2)"
                    : "var(--color-text-3)",
              }}
            >
              {isAd ? <Clapperboard className="h-4 w-4" aria-hidden /> : `E${ep.episode}`}
            </button>
            );
          })}
        </div>
      )}

      {canAddEpisode && currentProjectName ? (
        <EpisodeSourceDialog
          open={uploadOpen}
          projectName={currentProjectName}
          episodes={episodes}
          onClose={() => setUploadOpen(false)}
        />
      ) : null}

      {canAddEpisode && currentProjectName ? (
        <NovelSplitDialog open={splitOpen} projectName={currentProjectName} onClose={() => setSplitOpen(false)} />
      ) : null}

      {canAddEpisode && currentProjectName ? (
        <ConfirmDialog
          open={deleteTarget !== null}
          tone="danger"
          title={t("dashboard:episode_delete_title", { episode: deleteTarget ?? 0 })}
          description={t("dashboard:episode_delete_desc")}
          confirmLabel={t("dashboard:episode_delete")}
          loading={deleting}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={async () => {
            if (deleteTarget === null) return;
            setDeleting(true);
            try {
              await deleteEpisode(currentProjectName, deleteTarget);
              if (activeEp === deleteTarget) setLocation("/");
              setDeleteTarget(null);
            } catch (err) {
              useAppStore.getState().pushToast(err instanceof Error ? err.message : String(err), "error");
            } finally {
              setDeleting(false);
            }
          }}
        />
      ) : null}

      {/* ---- Collapse footer ---- */}
      <div
        className="flex items-center gap-2 px-2.5 py-2"
        style={{
          borderTop: "1px solid var(--color-hairline)",
          background: "oklch(0.17 0.010 250 / 0.6)",
        }}
      >
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="grid h-7 w-7 place-items-center rounded-md focus-ring"
          aria-expanded={!collapsed}
          style={{
            background: "oklch(0.24 0.012 250 / 0.5)",
            color: "var(--color-text-3)",
          }}
          title={collapsed ? t("dashboard:sidebar_expand") : t("dashboard:sidebar_collapse")}
          aria-label={
            collapsed ? t("dashboard:sidebar_expand") : t("dashboard:sidebar_collapse")
          }
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5" />
          ) : (
            <ChevronLeft className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
    </aside>
  );
}

import { request } from "./api";

export type GithubKind = "issues" | "prs";
export type GithubState = "open" | "closed" | "all";
export type GithubItem = {
  repo: string;
  kind: GithubKind;
  number: number;
  title: string;
  state: string;
  updated_at: string | null;
  url: string;
  author: string | null;
  labels: string[];
  draft?: boolean;
};
export type GithubComment = {
  id: number | string;
  author: string | null;
  body: string;
  created_at: string;
  url: string;
  context?: string;
};
export type GithubDetail = GithubItem & {
  body: string;
  comments: GithubComment[];
  available_labels: string[];
  head_sha: string | null;
  mergeable: boolean | null;
  mergeable_state: string | null;
  merged: boolean;
};
export type GithubCheck = { name: string; status: string; conclusion: string | null; url: string };
export type GithubRun = GithubCheck & { id: number };
export type GithubDetailResponse = { item: GithubDetail; checks: GithubCheck[]; runs: GithubRun[] };

const base = "/api/v1/github";
const target = (row: { repo: string; number: number; kind: GithubKind }) =>
  `${base}/${encodeURIComponent(row.repo)}/${row.kind}/${row.number}`;
const post = <T>(path: string, body: Record<string, unknown>) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const github = {
  repos: () => request<{ repos: { name: string; private: boolean }[] }>(`${base}/repos`),
  items: (params: { kind: GithubKind; state: GithubState; repo: string; query: string; page: number }) =>
    request<{ items: GithubItem[]; total_count: number; has_more: boolean }>(
      `${base}/items?${new URLSearchParams({ ...params, page: String(params.page) })}`,
    ),
  detail: (item: GithubItem) => request<GithubDetailResponse>(target(item)),
  comment: (item: GithubItem, body: string) => post<{ ok: boolean }>(`${target(item)}/comments`, { body }),
  state: (item: GithubItem, state: "open" | "closed") =>
    post<{ state: string }>(`${base}/${encodeURIComponent(item.repo)}/issues/${item.number}/state`, { state }),
  label: (item: GithubItem, label: string) =>
    post<{ labels: string[] }>(`${base}/${encodeURIComponent(item.repo)}/issues/${item.number}/labels`, { label }),
  merge: (item: GithubItem, head_sha: string, auto: boolean) =>
    post<{ ok: boolean }>(`${base}/${encodeURIComponent(item.repo)}/prs/${item.number}/merge`, { head_sha, auto }),
  rerun: (item: GithubItem, head_sha: string, run_id: number) =>
    post<{ ok: boolean }>(`${base}/${encodeURIComponent(item.repo)}/prs/${item.number}/rerun`, { head_sha, run_id }),
};

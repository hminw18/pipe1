import { useQuery } from '@tanstack/react-query'

import { get, type Schemas } from './client'

export const keys = {
  projects: ['projects'] as const,
  project: (id: string) => ['project', id] as const,
  businesses: (projectId: string) => ['businesses', projectId] as const,
  business: (id: string) => ['business', id] as const,
  reports: (businessId: string) => ['reports', businessId] as const,
  report: (id: string) => ['report', id] as const,
  video: (reportId: string) => ['video', reportId] as const,
  defects: (reportId: string) => ['defects', reportId] as const,
  gradeSummary: (reportId: string) => ['grade-summary', reportId] as const,
  jobs: (reportId: string, kind: string) => ['jobs', reportId, kind] as const,
  suggestions: (reportId: string) => ['suggestions', reportId] as const,
  taxonomy: ['taxonomy'] as const,
}

export const useProjects = (enabled = true) =>
  useQuery({
    queryKey: keys.projects,
    queryFn: () => get<Schemas['ProjectOut'][]>('/api/projects'),
    enabled,
  })

export const useProject = (id: string | undefined) =>
  useQuery({
    queryKey: keys.project(id ?? ''),
    queryFn: () => get<Schemas['ProjectOut']>(`/api/projects/${id}`),
    enabled: Boolean(id),
  })

export const useBusinesses = (projectId: string | undefined) =>
  useQuery({
    queryKey: keys.businesses(projectId ?? ''),
    queryFn: () => get<Schemas['BusinessOut'][]>(`/api/projects/${projectId}/businesses`),
    enabled: Boolean(projectId),
  })

export const useBusiness = (id: string | undefined) =>
  useQuery({
    queryKey: keys.business(id ?? ''),
    queryFn: () => get<Schemas['BusinessOut']>(`/api/businesses/${id}`),
    enabled: Boolean(id),
  })

export const useReports = (businessId: string | undefined) =>
  useQuery({
    queryKey: keys.reports(businessId ?? ''),
    queryFn: () => get<Schemas['ReportSummary'][]>(`/api/businesses/${businessId}/reports`),
    enabled: Boolean(businessId),
  })

export const useReport = (id: string | undefined) =>
  useQuery({
    queryKey: keys.report(id ?? ''),
    queryFn: () => get<Schemas['ReportDetail']>(`/api/reports/${id}`),
    enabled: Boolean(id),
  })

export const useTaxonomy = () =>
  useQuery({
    queryKey: keys.taxonomy,
    queryFn: () => get<Schemas['Taxonomy']>('/api/taxonomy'),
    staleTime: Infinity,
  })

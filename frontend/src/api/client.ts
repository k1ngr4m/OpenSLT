import axios from 'axios'
let refreshRequest: Promise<string> | undefined
export function refreshAccessToken(): Promise<string> {
  if (!refreshRequest) {
    refreshRequest = axios.post('/api/v1/auth/refresh', { refresh_token: localStorage.getItem('refresh_token') })
      .then(({ data }) => {
        localStorage.setItem('access_token', data.access_token)
        localStorage.setItem('refresh_token', data.refresh_token)
        return data.access_token as string
      }).finally(() => { refreshRequest = undefined })
  }
  return refreshRequest
}
export const api=axios.create({baseURL:'/api/v1',timeout:30000})
api.interceptors.request.use(config=>{const token=localStorage.getItem('access_token');if(token)config.headers.Authorization=`Bearer ${token}`;return config})
api.interceptors.response.use(response=>response,async error=>{const original=error.config;if(error.response?.status===401&&!original?._retry&&localStorage.getItem('refresh_token')){original._retry=true;try{const token=await refreshAccessToken();original.headers.Authorization=`Bearer ${token}`;return api(original)}catch{localStorage.clear();location.href='/login'}}return Promise.reject(error)})
export const errorMessage = (error: unknown) => {
  if (axios.isAxiosError<{ message?: string }>(error)) return error.response?.data?.message || error.message || '请求失败'
  return error instanceof Error ? error.message : '请求失败'
}

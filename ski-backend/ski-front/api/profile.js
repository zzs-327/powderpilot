import { request } from './request'

export function getProfile() {
  return request({ url: '/api/v1/profile', method: 'GET' })
}

export function updateProfile(data) {
  return request({ url: '/api/v1/profile', method: 'PUT', data })
}

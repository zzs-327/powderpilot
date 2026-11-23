import { request } from './request'

export function login(username, password) {
  return request({
    url: '/api/v1/user/login',
    method: 'POST',
    data: { username, password }
  })
}

export function register(username, password) {
  return request({
    url: '/api/v1/user/register',
    method: 'POST',
    data: { username, password, confirm_password: password }
  })
}

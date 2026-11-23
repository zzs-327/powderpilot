import { request } from './request'

export function userInfo() {
  return request({
    url: '/api/v1/user/info',
    method: 'get',
  })
}

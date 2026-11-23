import { request } from './request'

export function listResorts() {
  return request({ url: '/api/v1/resort', method: 'GET' })
}

export function resortRuns(resortKey, difficulty) {
  return request({
    url: '/api/v1/resort/' + resortKey + '/runs',
    method: 'GET',
    data: difficulty ? { difficulty } : {}
  })
}

export function resortPrices(resortKey, item) {
  return request({
    url: '/api/v1/resort/' + resortKey + '/prices',
    method: 'GET',
    data: item ? { item } : {}
  })
}

export function resortPhotos(resortKey) {
  return request({ url: '/api/v1/resort/' + resortKey + '/photos', method: 'GET' })
}

export function resortMedia(resortKey) {
  return request({ url: '/api/v1/resort/' + encodeURIComponent(resortKey) + '/media', method: 'GET' })
}

export function resortTips(resortKey) {
  return request({ url: '/api/v1/resort/' + encodeURIComponent(resortKey) + '/tips', method: 'GET' })
}

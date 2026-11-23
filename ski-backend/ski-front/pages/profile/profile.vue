<template>
	<view class="page">
		<view class="hero">
			<text class="hero-title">我的画像</text>
			<text class="hero-sub">画像决定推荐分层：新手/老手、单板/双板看到的内容不一样</text>
		</view>

		<view class="card">
			<picker mode="selector" :range="levels" @change="onPick('level', levels, $event)">
				<view class="field">
					<text class="label">水平</text>
					<text class="value">{{ form.level || '未设置' }} ›</text>
				</view>
			</picker>

			<picker mode="selector" :range="boards" @change="onPick('board_type', boards, $event)">
				<view class="field">
					<text class="label">单双板</text>
					<text class="value">{{ form.board_type || '未设置' }} ›</text>
				</view>
			</picker>

			<picker mode="selector" :range="regions" @change="onPick('home_region', regions, $event)">
				<view class="field">
					<text class="label">常活动区域</text>
					<text class="value">{{ form.home_region || '未设置' }} ›</text>
				</view>
			</picker>

			<picker mode="selector" :range="budgets" @change="onPick('budget_band', budgets, $event)">
				<view class="field last">
					<text class="label">预算带</text>
					<text class="value">{{ form.budget_band || '未设置' }} ›</text>
				</view>
			</picker>
		</view>

		<view class="card">
			<view class="field" @click="reload">
				<text class="label">从后端重新读取</text>
				<text class="value">刷新 ›</text>
			</view>
			<view class="field last" @click="logout">
				<text class="label danger">退出登录</text>
				<text class="value">›</text>
			</view>
		</view>

		<text class="tip">也可以在对话里直接说"我晋级了"或"我现在玩单板了"，Agent 会调用工具更新画像。</text>
	</view>
</template>

<script setup>
	import { ref } from 'vue'
	import { onShow } from '@dcloudio/uni-app'
	import { getProfile, updateProfile } from '@/api/profile.js'

	const levels = ['新手', '进阶', '老手']
	const boards = ['单板', '双板', '都玩']
	const regions = ['河南', '北京', '河北崇礼', '东北', '新疆', '其他']
	const budgets = ['500 以下', '500-1500', '1500-3000', '3000 以上']

	const form = ref({ level: '', board_type: '', home_region: '', budget_band: '' })

	const reload = async () => {
		try {
			const data = await getProfile()
			form.value = {
				level: data.level || '',
				board_type: data.board_type || '',
				home_region: data.home_region || '',
				budget_band: data.budget_band || ''
			}
		} catch (e) {
			// toast 已提示
		}
	}

	const onPick = async (field, options, event) => {
		const value = options[event.detail.value]
		const payload = {}
		payload[field] = value
		try {
			const data = await updateProfile(payload)
			form.value = {
				level: data.level || '',
				board_type: data.board_type || '',
				home_region: data.home_region || '',
				budget_band: data.budget_band || ''
			}
			uni.showToast({ title: '已保存', icon: 'none' })
		} catch (e) {
			// toast 已提示
		}
	}

	const logout = () => {
		uni.removeStorageSync('token')
		uni.removeStorageSync('lastTripCard')
		uni.reLaunch({ url: '/pages/login/login' })
	}

	onShow(() => {
		const token = uni.getStorageSync('token')
		if (!token) {
			uni.reLaunch({ url: '/pages/login/login' })
			return
		}
		reload()
	})
</script>

<style>
	/* 同对话页：H5 带 tabBar 时 height:100% 会把 tabBar 高度也算进去（见 index.vue 注释） */
	.page { height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px)); box-sizing: border-box; background-color: #f4f7fb; }
	.hero { background-color: #0f2a4a; padding: 36rpx 32rpx; }
	.hero-title { display: block; color: #ffffff; font-size: 40rpx; font-weight: bold; }
	.hero-sub { display: block; color: #9fc0e8; font-size: 24rpx; margin-top: 12rpx; line-height: 36rpx; }
	.card { background-color: #ffffff; margin: 20rpx 24rpx; border-radius: 18rpx; overflow: hidden; }
	.field { display: flex; align-items: center; justify-content: space-between; padding: 28rpx 24rpx; border-bottom: 1rpx solid #eef2f8; }
	.field.last { border-bottom: none; }
	.label { font-size: 28rpx; color: #1c2b3a; }
	.label.danger { color: #d9480f; }
	.value { font-size: 26rpx; color: #7a8aa0; }
	.tip { display: block; font-size: 24rpx; color: #9aa8bb; padding: 10rpx 32rpx 40rpx; line-height: 38rpx; }
</style>

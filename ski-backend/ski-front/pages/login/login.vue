<template>
	<view class="container">
		<view class="brand">🏔 PowderPilot</view>
		<view class="sub">反照骗的滑雪情报官</view>

		<view class="in">
			<input type="text" placeholder="用户名" v-model="username" />
			<input type="password" placeholder="密码" v-model="password" />
		</view>

		<!-- 按钮要真的 disabled：只换文案不拦点击的话，
		     手快连点两次就会发两次请求（注册第二次必然报"用户已存在"） -->
		<button class="primary" :disabled="loading" @click="doLogin">{{ loading ? '登录中…' : '登录' }}</button>
		<button class="ghost" :disabled="loading" @click="doRegister">{{ loading ? '注册中…' : '注册新账号' }}</button>

		<view class="tip">注册后会自动登录；token 有效期 24 小时。</view>
	</view>
</template>

<script setup>
	import { ref } from 'vue'
	import { login, register } from '@/api/auth.js'

	const username = ref('')
	const password = ref('')
	const loading = ref(false)

	const enter = (token) => {
		uni.setStorageSync('token', token)
		uni.reLaunch({ url: '/pages/index/index' })
	}

	const doLogin = async () => {
		if (!username.value || !password.value) {
			uni.showToast({ title: '请填写用户名和密码', icon: 'none' })
			return
		}
		loading.value = true
		try {
			enter(await login(username.value, password.value))
		} catch (e) {
			// request.js 已经弹过 toast
		} finally {
			loading.value = false
		}
	}

	const doRegister = async () => {
		if (!username.value || !password.value) {
			uni.showToast({ title: '请填写用户名和密码', icon: 'none' })
			return
		}
		loading.value = true
		try {
			await register(username.value, password.value)
			uni.showToast({ title: '注册成功，正在登录', icon: 'none' })
			enter(await login(username.value, password.value))
		} catch (e) {
			// 已存在用户等情况：toast 已提示，可直接去登录
		} finally {
			loading.value = false
		}
	}
</script>

<style>
	.container { display: flex; flex-direction: column; align-items: center; padding-top: 120rpx; height: 100%; background-color: #0f2a4a; }
	.brand { color: #ffffff; font-size: 52rpx; font-weight: bold; }
	.sub { color: #9fc0e8; font-size: 26rpx; margin-top: 12rpx; }
	.in { width: 78%; margin-top: 70rpx; }
	input { background-color: #ffffff; height: 88rpx; border-radius: 16rpx; margin-bottom: 24rpx; padding: 0 28rpx; font-size: 28rpx; }
	.primary { width: 78%; height: 88rpx; line-height: 88rpx; background-color: #1f6feb; color: #ffffff; font-size: 30rpx; border-radius: 16rpx; }
	.ghost { width: 78%; height: 88rpx; line-height: 88rpx; background-color: transparent; color: #cfe3ff; font-size: 28rpx; border-radius: 16rpx; border: 1rpx solid #3f6ea8; margin-top: 20rpx; }
	.tip { color: #7f9dc4; font-size: 22rpx; margin-top: 30rpx; }
</style>

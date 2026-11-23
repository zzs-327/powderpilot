<script>
	export default {
		onLaunch: function () {
			console.log('App Launch')
		},
		onShow: function () {
			console.log('App Show')
		},
		onHide: function () {
			console.log('App Hide')
		}
	}
</script>

<style>
	/* 让页面容器能撑满"除 tabBar 之外的剩余空间"，聊天页才能用 flex 布局 */
	page {
		height: 100%;
		background-color: #f4f7fb;
	}
</style>

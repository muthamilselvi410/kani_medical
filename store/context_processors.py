from .models import Cart, Notification

def cart_count(request):
    count = 0
    notif_count = 0
    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            count = cart.item_count
        except Cart.DoesNotExist:
            pass
        notif_count = Notification.objects.filter(user=request.user, is_read=False).count()
    return {'cart_item_count': count, 'notif_count': notif_count}
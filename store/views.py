import uuid
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Avg
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .models import *
from .forms import *


# ─── Home ───────────────────────────────────────────────────────────────────

def home(request):
    categories = Category.objects.all()
    featured_products = Product.objects.filter(is_active=True, is_featured=True)[:8]
    reviews = Review.objects.select_related('user', 'product').order_by('-created_at')[:3]
    return render(request, 'store/home.html', {
        'categories': categories,
        'featured_products': featured_products,
        'reviews': reviews,
    })


# ─── Auth ────────────────────────────────────────────────────────────────────

def signup_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = SignUpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        UserProfile.objects.create(user=user, phone=form.cleaned_data.get('phone', ''))
        Cart.objects.create(user=user)
        login(request, user)
        Notification.objects.create(user=user, message="Welcome to Kani Medical! Start shopping for your health needs.")
        messages.success(request, "Account created successfully! Welcome to Kani Medical.")
        return redirect('home')
    return render(request, 'store/signup.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        messages.success(request, f"Welcome back, {user.first_name or user.username}!")
        return redirect(request.GET.get('next', 'home'))
    return render(request, 'store/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect('home')


# ─── Products ────────────────────────────────────────────────────────────────

def products(request):
    qs = Product.objects.filter(is_active=True)
    category_slug = request.GET.get('category')
    query = request.GET.get('q', '')
    sort = request.GET.get('sort', '')
    selected_category = None

    if category_slug:
        selected_category = get_object_or_404(Category, slug=category_slug)
        qs = qs.filter(category=selected_category)
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(description__icontains=query))
    if sort == 'price_asc':
        qs = qs.order_by('price')
    elif sort == 'price_desc':
        qs = qs.order_by('-price')
    elif sort == 'newest':
        qs = qs.order_by('-created_at')

    categories = Category.objects.all()
    return render(request, 'store/products.html', {
        'products': qs,
        'categories': categories,
        'selected_category': selected_category,
        'query': query,
        'sort': sort,
    })


def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    reviews = product.reviews.select_related('user').order_by('-created_at')
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0
    related = Product.objects.filter(category=product.category, is_active=True).exclude(pk=product.pk)[:4]
    review_form = ReviewForm()

    if request.method == 'POST' and request.user.is_authenticated:
        review_form = ReviewForm(request.POST)
        if review_form.is_valid():
            r = review_form.save(commit=False)
            r.user = request.user
            r.product = product
            r.save()
            messages.success(request, "Review submitted!")
            return redirect('product_detail', slug=slug)

    return render(request, 'store/product_detail.html', {
        'product': product,
        'reviews': reviews,
        'avg_rating': round(avg_rating, 1),
        'related': related,
        'review_form': review_form,
    })


# ─── Cart ────────────────────────────────────────────────────────────────────

@login_required
def cart(request):
    cart_obj, _ = Cart.objects.get_or_create(user=request.user)
    return render(request, 'store/cart.html', {'cart': cart_obj})


@login_required
@require_POST
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    cart_obj, _ = Cart.objects.get_or_create(user=request.user)
    item, created = CartItem.objects.get_or_create(cart=cart_obj, product=product)
    if not created:
        item.quantity += 1
        item.save()
    messages.success(request, f"{product.name} added to cart!")
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'count': cart_obj.item_count})
    return redirect('cart')


@login_required
def update_cart(request, item_id):
    item = get_object_or_404(CartItem, pk=item_id, cart__user=request.user)
    qty = int(request.POST.get('quantity', 1))
    if qty < 1:
        item.delete()
    else:
        item.quantity = qty
        item.save()
    return redirect('cart')


@login_required
def remove_from_cart(request, item_id):
    item = get_object_or_404(CartItem, pk=item_id, cart__user=request.user)
    item.delete()
    messages.success(request, "Item removed from cart.")
    return redirect('cart')


# ─── Checkout / Orders ───────────────────────────────────────────────────────

DELIVERY_CHARGE = 50          # ₹50 delivery fee
FREE_DELIVERY_ABOVE = 500    # Free delivery on orders above ₹500


@login_required
def checkout(request):
    cart_obj, _ = Cart.objects.get_or_create(user=request.user)
    if not cart_obj.items.exists():
        messages.warning(request, "Your cart is empty.")
        return redirect('cart')

    addresses = Address.objects.filter(user=request.user)
    address_form = AddressForm()

    subtotal = cart_obj.total
    delivery_charge = 0 if subtotal >= FREE_DELIVERY_ABOVE else DELIVERY_CHARGE
    grand_total = subtotal + delivery_charge

    if request.method == 'POST':
        address_id = request.POST.get('address_id')
        payment_method = request.POST.get('payment_method', 'cod')

        if address_id:
            address = get_object_or_404(Address, pk=address_id, user=request.user)
        else:
            address_form = AddressForm(request.POST)
            if address_form.is_valid():
                address = address_form.save(commit=False)
                address.user = request.user
                address.save()
            else:
                return render(request, 'store/checkout.html', {
                    'cart': cart_obj, 'addresses': addresses, 'address_form': address_form,
                    'delivery_charge': delivery_charge, 'grand_total': grand_total,
                    'subtotal': subtotal, 'free_delivery_above': FREE_DELIVERY_ABOVE,
                })

        order_number = 'KM' + uuid.uuid4().hex[:8].upper()
        order = Order.objects.create(
            user=request.user,
            order_number=order_number,
            address=address,
            payment_method=payment_method,
            total_amount=grand_total,
        )
        for ci in cart_obj.items.all():
            OrderItem.objects.create(order=order, product=ci.product, quantity=ci.quantity, price=ci.product.price)
            ci.product.stock = max(0, ci.product.stock - ci.quantity)
            ci.product.save()
        cart_obj.items.all().delete()

        Notification.objects.create(
            user=request.user,
            message=f"Order #{order_number} placed successfully! We'll notify you when it's shipped."
        )
        messages.success(request, f"Order #{order_number} placed successfully!")
        return redirect('order_detail', order_number=order_number)

    return render(request, 'store/checkout.html', {
        'cart': cart_obj,
        'addresses': addresses,
        'address_form': address_form,
        'subtotal': subtotal,
        'delivery_charge': delivery_charge,
        'grand_total': grand_total,
        'free_delivery_above': FREE_DELIVERY_ABOVE,
    })


@login_required
def orders(request):
    order_list = Order.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'store/orders.html', {'orders': order_list})


@login_required
def order_detail(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    return render(request, 'store/order_detail.html', {'order': order})


@login_required
def cancel_order(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    if order.status in ('pending', 'confirmed'):
        order.status = 'cancelled'
        order.save()
        messages.success(request, f"Order #{order_number} cancelled.")
    else:
        messages.error(request, "Order cannot be cancelled at this stage.")
    return redirect('order_detail', order_number=order_number)


# ─── Profile ─────────────────────────────────────────────────────────────────

@login_required
def profile(request):
    profile_obj, _ = UserProfile.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=profile_obj)
        if form.is_valid():
            request.user.first_name = form.cleaned_data['first_name']
            request.user.last_name = form.cleaned_data['last_name']
            request.user.email = form.cleaned_data['email']
            request.user.save()
            form.save()
            messages.success(request, "Profile updated successfully!")
            return redirect('profile')
    else:
        form = ProfileForm(instance=profile_obj, initial={
            'first_name': request.user.first_name,
            'last_name': request.user.last_name,
            'email': request.user.email,
        })
    addresses = Address.objects.filter(user=request.user)
    notifs = Notification.objects.filter(user=request.user).order_by('-created_at')[:10]
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, 'store/profile.html', {'form': form, 'addresses': addresses, 'notifications': notifs})


@login_required
def add_address(request):
    if request.method == 'POST':
        form = AddressForm(request.POST)
        if form.is_valid():
            addr = form.save(commit=False)
            addr.user = request.user
            addr.save()
            messages.success(request, "Address added.")
    return redirect('profile')


@login_required
def delete_address(request, pk):
    addr = get_object_or_404(Address, pk=pk, user=request.user)
    addr.delete()
    messages.success(request, "Address deleted.")
    return redirect('profile')


# ─── Static Pages ────────────────────────────────────────────────────────────

def about(request):
    return render(request, 'store/about.html')


def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Message sent! We'll get back to you within 24 hours.")
        return redirect('contact')
    return render(request, 'store/contact.html', {'form': form})


def search(request):
    query = request.GET.get('q', '')
    results = Product.objects.filter(is_active=True).filter(
        Q(name__icontains=query) | Q(description__icontains=query) | Q(category__name__icontains=query)
    ) if query else Product.objects.none()
    return render(request, 'store/search.html', {'results': results, 'query': query})